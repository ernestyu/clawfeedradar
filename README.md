# clawfeedradar

Personal "reading radar" built on top of `clawsqlite`: it pulls articles from Hacker News / RSS / arXiv and ranks them against your existing knowledge base, generating a personalized RSS feed with optional bilingual summaries.

> Goal: simple, controllable, auditable, and loosely coupled with `clawsqlite`. This document describes the **current v1 implementation** (branch `bot/20260402-embedding`).

---

## Overview

clawfeedradar does three things:

1. **Fetch candidates from external sources**  
   Currently HN RSS and generic RSS are supported; arXiv and others can be added later.
2. **Score candidates using `clawsqlite` interest clusters**  
   It uses embeddings + interest clusters to estimate how well each candidate matches your long-term interests, then applies light recency / popularity bias.
3. **Generate an RSS feed with optional bilingual content**  
   For selected items it scrapes fulltext, calls a small LLM to produce preview summary + bilingual body, and writes an XML + JSON pair.

In short: **`clawsqlite` knows what you like; `clawfeedradar` goes out, finds similar content, and feeds it to your RSS reader.**

---

## Quick start

### 1. Prepare environment

Assume a workspace like:

```text
~/.openclaw/workspace/
  ├── clawsqlite          # clawsqlite repo
  ├── knowledge_data      # your clawsqlite-knowledge DB
  ├── clawfeedradar       # this repo
  └── clawfetch / ...     # fulltext scraper + wrapper
```

Make sure `clawsqlite` has built interest clusters:

```bash
cd ~/.openclaw/workspace/clawsqlite
clawsqlite knowledge build-interest-clusters \
  --root ~/.openclaw/workspace/knowledge_data
```

### 2. Configure clawfeedradar

```bash
cd ~/.openclaw/workspace/clawfeedradar
cp ENV.example .env
# edit .env for your local setup
```

You need to configure at least:

- clawsqlite knowledge base
  - `CLAWSQLITE_ROOT` pointing to `knowledge_data`
  - `CLAWSQLITE_DB` if you want an explicit sqlite path
- Embedding service (shared with clawsqlite)
  - `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_API_KEY`
  - `CLAWSQLITE_VEC_DIM` must match the embedding model dimension
- Output directory
  - `CLAWFEEDRADAR_OUTPUT_DIR` for XML/JSON output
- Fulltext scraping
  - `CLAWFEEDRADAR_SCRAPE_CMD` pointing to a wrapper that calls `clawfetch`
  - `CLAWFEEDRADAR_SCRAPE_WORKERS` controlling parallelism (per-host serialization still applies)
- Small LLM (summaries + bilingual body)
  - `SMALL_LLM_BASE_URL` / `SMALL_LLM_MODEL` / `SMALL_LLM_API_KEY`
  - `CLAWFEEDRADAR_LLM_CONTEXT_TOKENS` (approx token budget, internally converted to char budget)
  - `CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS` (screen-sized bilingual segments)
  - `CLAWFEEDRADAR_LLM_SLEEP_BETWEEN_MS`
  - `CLAWFEEDRADAR_LLM_SOURCE_LANG` / `CLAWFEEDRADAR_LLM_TARGET_LANG`
- Scoring weights
  - `CLAWFEEDRADAR_W_RECENCY`
  - `CLAWFEEDRADAR_W_POPULARITY`
  - `CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS`
  - `CLAWFEEDRADAR_INTEREST_SIGMOID_K` (steepness of S-shaped stretching around 0.5)
- Default item count
  - `CLAWFEEDRADAR_MAX_ITEMS` as the default when `--max-items` is omitted

### 3. Run a single feed (debug)

```bash
cd ~/.openclaw/workspace/clawfeedradar
python -m clawfeedradar.cli run \
  --root ~/.openclaw/workspace/orgmode/clawsqlite/data \
  --url https://feeds.bbci.co.uk/news/technology/rss.xml \
  --output ./feeds/bbc-tech.xml \
  --max-source-items 15 \
  --score-threshold 0.4 \
  --max-items 12 \
  --source-lang en \
  --target-lang zh
```

This produces:

- `./feeds/bbc-tech.xml`  
  RSS feed where `<description>` combines:
  - `summary_preview` (short summary in target language) and
  - `body_bilingual` (full bilingual body, screen-sized segments).
- `./feeds/bbc-tech.json`  
  Sidecar JSON containing `fulltext`, `summary_preview`, `body_bilingual`, and scoring details including:
  - `interest_score` (S-shaped stretched score)
  - `interest_score_raw` (linear interest score)
  - `final_score`
  - `best_cluster_id` / `best_cluster_weight`

---

## Configuration (env overview)

See `ENV.example` for the full set of variables. The most important groups are:

- clawsqlite (`CLAWSQLITE_ROOT`, `CLAWSQLITE_DB`)
- Embedding (`EMBEDDING_*`, `CLAWSQLITE_VEC_DIM`, `CLAWSQLITE_INTEREST_TAG_WEIGHT`)
- Radar output and scraping (`CLAWFEEDRADAR_OUTPUT_DIR`, `CLAWFEEDRADAR_SCRAPE_CMD`, `CLAWFEEDRADAR_SCRAPE_WORKERS`)
- Scoring (`CLAWFEEDRADAR_W_RECENCY`, `CLAWFEEDRADAR_W_POPULARITY`, `CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS`, `CLAWFEEDRADAR_INTEREST_SIGMOID_K`)
- Small LLM (`SMALL_LLM_*`, `CLAWFEEDRADAR_LLM_CONTEXT_TOKENS`, `CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS`, `CLAWFEEDRADAR_LLM_TAG_MAX_PER_ITEM`, `CLAWFEEDRADAR_LLM_SLEEP_BETWEEN_MS`, `CLAWFEEDRADAR_LLM_SOURCE_LANG`, `CLAWFEEDRADAR_LLM_TARGET_LANG`)
- Default selection size (`CLAWFEEDRADAR_MAX_ITEMS`).

---

## Scoring (current v1)

### Interest vectors

For each candidate:

1. Fetch fulltext (with per-host serialization and retries).
2. Build a *long summary* via `_build_long_summary(fulltext)`:
   - Split by blank lines into paragraphs.
   - Accumulate from the top until roughly 1200 characters, at paragraph boundaries.
   - If the last non-empty paragraph is not included, append it.
   - If fulltext is missing, fall back to `title + "\n\n" + summary`.
3. Use a small LLM to generate tags for all long summaries in batches (`generate_tags_bulk`).
4. Embed long summaries and tag texts via the serial embedding client with retries and rate limiting.
5. Mix summary/tag embeddings into *interest vectors* using `CLAWSQLITE_INTEREST_TAG_WEIGHT` (same semantics as on the clawsqlite side).

### Interest score

1. Load clusters from clawsqlite: `ClusterInfo(id, label, size, centroid)`.
2. Compute cluster weights:

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
       sim = max(sim, 0.0)  # negative similarities are clamped to 0
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

4. Apply an S-shaped logistic sigmoid centered at 0.5:

   ```python
   # k from CLAWFEEDRADAR_INTEREST_SIGMOID_K (default 4.0)
   z = k * (interest_raw - 0.5)
   interest = 1.0 / (1.0 + exp(-z))
   ```

Both `interest_raw` and `interest` are stored in the JSON sidecar:

- `interest_score_raw`: linear interest score;
- `interest_score`: sigmoid-stretched score used for thresholding.

### Recency and popularity bias

After sigmoid:

```python
rec = recency_weight(published_at, now, half_life_seconds)
# half_life_seconds = CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS * 24 * 3600

pop = clamp(candidate.popularity_score, 0.0, 1.0)

interest_bias = w_recency * rec + w_popularity * pop
biased_interest = interest + interest_bias
```

Defaults:

- `w_recency = w_popularity = 0.05` (light bias);
- non-HN RSS sources default to `popularity_score = 0.0`.

Per-run overrides are available via `--w-recency` and `--w-popularity`.

### Source-specific extras and final score

A thin source-specific channel is allowed for HN/arxiv:

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

`final_score` is used for sorting.

---

## CLI

### `clawfeedradar run`

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

- `--root` overrides `CLAWSQLITE_ROOT`.
- `--max-items` overrides `CLAWFEEDRADAR_MAX_ITEMS`.
- `--max-source-items` limits how many feed entries are pulled *before* scoring.
- `--w-recency` / `--w-popularity` override the env/default bias weights.
- `--feed-title` sets the RSS `<title>`.
- `--no-preview` disables preview summary LLM (debug/fast mode).
- `--preview-words` controls preview summary length (in **words**, not characters).
- `--json` also prints selected items as JSON to stdout.

### `clawfeedradar schedule`

Multi-source scheduling based on `sources.json`:

```bash
clawfeedradar schedule \
  --root /path/to/knowledge_data \
  --sources-json /path/to/sources.json \
  --output-dir ./feeds
```

Each entry in `sources.json` looks like:

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

For each due source:

- Run the same pipeline as `run`, with `max_items = max_entries`.
- Write `{label}.xml` + `{label}.json` into `output-dir`.
- Update `last_success_at` / `last_error`.

---

For deeper design rationale (interest clusters, scoring, source adapters), see `docs/SPEC.md` and `docs/DESIGN.md`.
