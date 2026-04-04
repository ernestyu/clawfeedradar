# clawfeedradar SPEC (v1)

> This document describes the **current implementation** (branch `bot/20260402-embedding`).
> Older v0 designs (`W_SIM_BEST`/`border`/`sources.txt`) are obsolete; see git history if needed.

Scope:

- Single-user radar built on top of an existing `clawsqlite` knowledge base (`interest_clusters`).
- Fetch candidates from HN / RSS / other sources.
- Score candidates using interest clusters + recency + popularity.
- Output per-source RSS XML + JSON.
- Optional small-LLM based preview summaries and bilingual bodies.

---

## 1. Goals

- Let `clawsqlite` interest clusters be the **only source of the interest space**.
- clawfeedradar only does: fetch data → normalize into `Candidate` → score against interest space → emit feeds.
- Scoring should be **interpretable**, **tunable**, and **failure-tolerant** (no brittle single-point crashes).


## 2. Boundary with `clawsqlite`

### 2.1 `clawsqlite` side (KB + interest clusters)

`clawsqlite knowledge build-interest-clusters` is responsible for:

- Reading article vectors from `articles` / `articles_vec` / `articles_tag_vec`.
- Mixing summary & tag embeddings using `CLAWSQLITE_INTEREST_TAG_WEIGHT` (default 0.75):

  ```text
  v_interest = (1 - w_tag) * v_summary + w_tag * v_tag
  ```

- Running k-means + small-cluster merge on `v_interest`, writing:
  - `interest_clusters(id, label, size, centroid, ...)`
  - `interest_cluster_members(cluster_id, article_id, ...)`.
- After success, writing into `interest_meta`:
  - `key='interest_clusters_last_built_at', value=<UTC ISO8601>`.

clawfeedradar only reads:

- `interest_clusters` (cluster center + size).
- `interest_meta` (for staleness warnings).

### 2.2 clawfeedradar side

Responsibilities:

1. Fetch candidates from external sources and normalize into `Candidate`.
2. Map candidates into the clawsqlite interest space (interest vectors).
3. Score candidates (interest main channel + recency/popularity bias + source-specific extras).
4. Select top-N, fetch fulltext, run LLM preview/bilingual, and output XML + JSON.

clawfeedradar **does not modify** the clawsqlite schema; it only depends on:

- `CLAWSQLITE_ROOT` / `CLAWSQLITE_DB`.
- `EMBEDDING_*` / `CLAWSQLITE_VEC_DIM`.
- `interest_clusters` / `interest_meta`.


## 3. Data models

### 3.1 `Candidate`

```python
@dataclass
class Candidate:
    id: str
    url: str
    title: str
    summary: str
    tags: str
    source: str              # "hackernews" | "arxiv" | "rss" | ...
    published_at: datetime
    popularity_score: float  # 0..1, normalized by source adapter
    source_meta: dict[str, Any]
```

Guidance:

- `popularity_score` is set to a real signal when available (e.g. HN points/comments).
  - For plain RSS sources, the default is 0.0 (we no longer fabricate a neutral 0.5).
- `source_meta` is only for source-specific scoring; the main scoring pipeline only looks at `popularity_score`.

### 3.2 `ClusterInfo` / `InterestMatch` / `ScoredItem`

```python
@dataclass
class ClusterInfo:
    id: int
    label: str
    size: int
    centroid: list[float]


@dataclass
class InterestMatch:
    best_cluster_id: int
    sim_best: float
    sim_second: float
    best_cluster_weight: float   # size_k / total_size


@dataclass
class ScoredItem:
    candidate: Candidate
    interest_score: float        # sigmoid-stretched interest score
    interest_score_raw: float    # linear interest score (cluster-weighted similarity)
    final_score: float
    match: InterestMatch
```

---

## 4. Environment variables (v1)

Only the most relevant ones are listed here; see `ENV.example` for the full set.

### 4.1 clawsqlite + embedding

```env
CLAWSQLITE_ROOT=/path/to/clawsqlite/data
CLAWSQLITE_DB=/path/to/clawkb.sqlite3

EMBEDDING_BASE_URL=...
EMBEDDING_MODEL=...
EMBEDDING_API_KEY=...
CLAWSQLITE_VEC_DIM=1024

CLAWSQLITE_INTEREST_TAG_WEIGHT=0.75   # 0..1, controls summary/tag mixing
```

### 4.2 Fetching & output

```env
CLAWFEEDRADAR_OUTPUT_DIR=/path/to/feeds
CLAWFEEDRADAR_SCRAPE_CMD="/path/to/clawfetch_wrapper.sh"
CLAWFEEDRADAR_SCRAPE_WORKERS=4
# CLAWFEEDRADAR_HTTP_USER_AGENT=...

CLAWFEEDRADAR_MAX_ITEMS=12   # default when --max-items is omitted
```

Fetching strategy:

- `ThreadPoolExecutor(max_workers=CLAWFEEDRADAR_SCRAPE_WORKERS)`.
- Per-host serialization using a host lock; cross-host concurrency allowed.
- `fetch_fulltext` handles retries and backoff internally.

### 4.3 Scoring

```env
CLAWFEEDRADAR_W_RECENCY=0.05
CLAWFEEDRADAR_W_POPULARITY=0.05
CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS=3

# S-shaped sigmoid steepness around 0.5 for interest_score.
CLAWFEEDRADAR_INTEREST_SIGMOID_K=4.0

# Source-specific extras (used in compute_final_score).
CLAWFEEDRADAR_LAMBDA_HN=0.2
CLAWFEEDRADAR_LAMBDA_ARXIV=0.1
```

### 4.4 LLM

```env
SMALL_LLM_BASE_URL=...
SMALL_LLM_MODEL=...
SMALL_LLM_API_KEY=...

# Token-based context budget; internally multiplied by ~4 for a char budget.
CLAWFEEDRADAR_LLM_CONTEXT_TOKENS=8096

# Max characters per bilingual screen/segment.
CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS=2400

# Max tags per item (prompt hint only).
CLAWFEEDRADAR_LLM_TAG_MAX_PER_ITEM=12

# Simple rate limiting between LLM calls (milliseconds).
CLAWFEEDRADAR_LLM_SLEEP_BETWEEN_MS=500

# Language hints.
CLAWFEEDRADAR_LLM_SOURCE_LANG=auto
CLAWFEEDRADAR_LLM_TARGET_LANG=zh
```

---

## 5. Source adapters

Unified interface:

```python
def fetch_candidates_from_source(source_type: str, source_url: str, max_items: int | None = None) -> list[Candidate]:
    ...
```

### 5.1 RSS adapter (`sources/rss.py`)

- Uses `feedparser.parse(source_url)`.
- Extracts `link` / `title` / `summary/description` / `tags` / `published_at`.
- Infers `source`:
  - Feed URL host contains `hnrss.org` or `news.ycombinator.com` → `source="hackernews"`.
  - Entry link host contains `arxiv.org` → `source="arxiv"`.
  - Otherwise `source="rss"`.
- Popularity:

  ```python
  pop = 0.0
  if source == "hackernews":
      # parse "123 points | 45 comments" from title/summary
      # normalize into [0,1] and store in popularity_score
  ```

HN/arxiv-specific adapters can be added later if needed, but must still emit `Candidate` objects.

---

## 6. Scoring

### 6.1 Interest vectors

For each filtered candidate:

1. Build a long summary via `_build_long_summary(fulltext)`:
   - Split fulltext into paragraphs by blank lines.
   - Accumulate from the top until ~1200 characters (paragraph boundaries only).
   - If the last non-empty paragraph is not included, append it.
   - If fulltext is missing, fall back to `title + "\n\n" + summary`.
2. Generate tags via a small LLM using `generate_tags_bulk`, supporting partial success + retries.
3. Embed long summaries and tag texts using the serial embedding client (with retries and rate limiting).
4. Mix summary/tag embeddings into an interest vector using `CLAWSQLITE_INTEREST_TAG_WEIGHT` (same semantics as on the clawsqlite side).

### 6.2 Interest score

1. Load clusters from clawsqlite: `ClusterInfo(id, label, size, centroid)`.
2. Precompute cluster weights:

   ```text
   total_size = Σ_k max(1, size_k)
   cluster_weight_k = size_k / total_size
   ```

3. For each candidate interest vector `emb`:

   ```python
   interest_raw = 0.0
   best_cluster_id = -1
   best_sim = 0.0
   second_sim = 0.0

   for cluster in clusters:
       sim = cosine(emb, cluster.centroid)
       sim = max(sim, 0.0)
       w = cluster_weights[cluster.id]
       interest_raw += w * sim

       if sim > best_sim:
           second_sim = best_sim
           best_sim = sim
           best_cluster_id = cluster.id
       elif sim > second_sim:
           second_sim = sim

   best_cluster_weight = cluster_weights[best_cluster_id]
   ```

4. Apply an S-shaped logistic sigmoid around 0.5:

   ```python
   # k from CLAWFEEDRADAR_INTEREST_SIGMOID_K (default 4.0)
   z = k * (interest_raw - 0.5)
   interest = 1.0 / (1.0 + exp(-z))
   ```

Both `interest_raw` and `interest` are written into `ScoredItem` and the JSON sidecar:

- `interest_score_raw`: linear interest score.
- `interest_score`: sigmoid-stretched interest score used for thresholding.

### 6.3 Recency and popularity bias

On top of the sigmoid-stretched `interest`:

```python
rec = recency_weight(published_at, now, half_life_seconds)
# half_life_seconds = CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS * 24 * 3600

pop = clamp(candidate.popularity_score, 0.0, 1.0)

interest_bias = w_recency * rec + w_popularity * pop
biased_interest = interest + interest_bias
```

Defaults:

- `w_recency = w_popularity = 0.05`.
- For plain RSS sources, `popularity_score = 0.0` (no fake neutral 0.5).

Per-run overrides are exposed via CLI: `--w-recency`, `--w-popularity`.

### 6.4 Source-specific extras and final score

For a small set of sources (currently HN), a source-specific channel is allowed:

```python
def score_generic_extra(cand, base):
    return 0.0


def score_hn_extra(cand, base):
    meta = cand.source_meta or {}
    points = float(meta.get("hn_points", 0) or 0)
    comments = float(meta.get("hn_comments", 0) or 0)
    s_points = min(1.0, points / 500.0)
    s_comments = min(1.0, comments / 100.0)
    return 0.5 * s_points + 0.5 * s_comments


SOURCE_SCORERS = {
    "hackernews": score_hn_extra,
    "arxiv": score_arxiv_extra,   # currently returns 0.0
}

LAMBDA_SOURCE = {
    "hackernews": λ_hn,   # default 0.2
    "arxiv": λ_arxiv,     # default 0.1
    "default": 0.1,
}


def compute_final_score(cand, interest_score):
    extra_fn = SOURCE_SCORERS.get(cand.source, score_generic_extra)
    lam = LAMBDA_SOURCE.get(cand.source, LAMBDA_SOURCE["default"])
    extra = float(extra_fn(cand, interest_score) or 0.0)
    return interest_score + lam * extra
```

`final_score` is used for sorting and selection.

---

## 7. Selection & output

### 7.1 Selection

Given a list of `ScoredItem`:

1. Sort by `final_score` descending.
2. Filter by `interest_score >= score_threshold`.
3. Take the first `max_items` entries.

v1 does **not** implement complex diversity/exploration strategies; adding per-cluster quotas or boundary-exploration items is left for a future version.

### 7.2 JSON sidecar

Each selected item in the JSON sidecar looks like:

```jsonc
{
  "id": "...",
  "url": "https://...",
  "title": "...",
  "summary": "...",
  "source": "rss",
  "published_at": "2026-04-04T09:34:49+00:00",
  "popularity_score": 0.0,
  "interest_score": 0.52,
  "interest_score_raw": 0.48,
  "final_score": 0.56,
  "best_cluster_id": 9,
  "best_cluster_weight": 0.16,
  "fulltext": "...",
  "summary_preview": "...",
  "body_bilingual": "..."
}
```

---

## 8. CLI & scheduling

### 8.1 `clawfeedradar run`

Single-source mode:

```bash
clawfeedradar run \
  --root /path/to/knowledge_data \
  --url https://example.com/feed.xml \
  --output ./feeds/example.xml \
  --score-threshold 0.4 \
  --max-items 12 \
  --max-source-items 50 \
  --w-recency 0.05 \
  --w-popularity 0.05 \
  --feed-title "My Radar" \
  --source-lang en \
  --target-lang zh \
  --preview-words 512 \
  --json
```

CLI precedence rules:

- `--root` > `CLAWSQLITE_ROOT`.
- `--max-items` > `CLAWFEEDRADAR_MAX_ITEMS` > default 12.
- `--w-recency` / `--w-popularity` > env > defaults.

### 8.2 `clawfeedradar schedule`

Multi-source scheduling based on `sources.json`:

```bash
clawfeedradar schedule \
  --root /path/to/knowledge_data \
  --sources-json /path/to/sources.json \
  --output-dir ./feeds
```

Example entry in `sources.json`:

```jsonc
{
  "label": "bbc-tech",
  "url": "https://feeds.bbci.co.uk/news/technology/rss.xml",
  "interval_hours": 8,
  "max_entries": 15,
  "score_threshold": 0.4,
  "source_lang": "en",
  "target_lang": "zh",
  "last_success_at": null,
  "last_error": null
}
```

Scheduling behavior:

- When `last_success_at` is null or older than `interval_hours`, the entry is considered due.
- The same pipeline as `run` is executed with `max_items = max_entries`.
- `{label}.xml` + `{label}.json` are written into `output-dir`.
- `last_success_at` / `last_error` are updated and written back to `sources.json`.

---

## 9. Publishing feeds via Git (GitHub Pages / Gitee Pages)

clawfeedradar can optionally push the generated XML/JSON to a git repository,
so that GitHub Pages or Gitee Pages can host your feed at a stable HTTPS URL.

### 9.1 Configuration

Set the following env vars in your `.env`:

```env
CLAWFEEDRADAR_PUBLISH_GIT_REPO=git@github.com:yourname/clawfeedradar-feed.git
CLAWFEEDRADAR_PUBLISH_GIT_BRANCH=gh-pages
CLAWFEEDRADAR_PUBLISH_GIT_PATH=feeds
```

or for Gitee:

```env
CLAWFEEDRADAR_PUBLISH_GIT_REPO=git@gitee.com:yourname/clawfeedradar-feed.git
CLAWFEEDRADAR_PUBLISH_GIT_BRANCH=gh-pages
CLAWFEEDRADAR_PUBLISH_GIT_PATH=feeds
```

Requirements:

- The repo must exist and be accessible via git (SSH or HTTPS with credentials).
- On GitHub/Gitee, enable Pages for this repo and point it to the chosen
  branch/path (e.g. `gh-pages` / `feeds/`).
- Ensure the environment has git installed and proper authentication:
  - SSH keys configured for `git@github.com` or `git@gitee.com`, or
  - a credential helper for HTTPS.

### 9.2 Behavior

After each successful pipeline run (`run` or `schedule`), clawfeedradar will:

1. Clone (or reuse) the configured repo under `./.publish/<slug>/`.
2. Checkout the configured branch, creating it if needed.
3. Copy the generated `*.xml` and `*.json` into `<repo>/<PATH>/`.
4. Run `git add`, `git commit` (ignored if nothing changed), and `git push`.

If publishing is not configured (no `CLAWFEEDRADAR_PUBLISH_GIT_REPO`) it does nothing.

On failures (clone/checkout/push), it:

- Prints a `[error] ...` message to stdout with a suggested next action;
- Logs the error via the `clawfeedradar` logger;
- Returns a non-zero exit code from the publish step, but **does not crash the
  main scoring pipeline** (the run still succeeds locally; only remote publish fails).

This makes it easy to use GitHub Pages or Gitee Pages as a free, static host
for your RSS feeds, while keeping clawfeedradar's core behavior independent
of any specific provider.
