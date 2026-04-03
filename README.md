# clawfeedradar

Personal "reading radar" built on top of `clawsqlite`: it pulls articles from Hacker News / RSS / arXiv and ranks them against your existing knowledge base, generating a personalized RSS feed with bilingual summaries.

> Goal: simple, controllable, auditable, and loosely coupled with `clawsqlite`. This document describes the current v0 implementation.

---

## Overview

clawfeedradar does three things:

1. **Fetch candidates from external sources**  
   Currently HN RSS and generic RSS are supported; arXiv and others can be added later.
2. **Score candidates using `clawsqlite` interest clusters**  
   It uses embeddings + interest clusters to estimate how well each candidate matches your long-term interests, and combines that with recency and popularity.
3. **Generate an RSS feed with bilingual content**  
   For selected items it scrapes fulltext, calls a small LLM to produce bilingual (original + translation) body, and writes an XML + JSON pair.

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
clawsqlite knowledge build-interest-clusters   --root ~/.openclaw/workspace/knowledge_data
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
- Small LLM (summaries + bilingual body)
  - `SMALL_LLM_BASE_URL` / `SMALL_LLM_MODEL` / `SMALL_LLM_API_KEY`
  - `CLAWFEEDRADAR_LLM_MAX_OUTPUT_CHARS` and `CLAWFEEDRADAR_LLM_SLEEP_BETWEEN_MS`
  - `CLAWFEEDRADAR_LLM_SOURCE_LANG` / `CLAWFEEDRADAR_LLM_TARGET_LANG`
  - `CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS` (one screen per bilingual segment, e.g. 2400 chars)
- Fulltext scraping
  - `CLAWFEEDRADAR_SCRAPE_CMD` pointing to a wrapper that calls `clawfetch`
  - `CLAWFEEDRADAR_SCRAPE_WORKERS` controlling parallelism (per-host serialization still applies)
- Scoring weights
  - `CLAWFEEDRADAR_W_SIM_BEST`
  - `CLAWFEEDRADAR_W_SIM_SECOND`
  - `CLAWFEEDRADAR_W_RECENCY`
  - `CLAWFEEDRADAR_W_POPULARITY`
  - `CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS` (recency half-life in days)
- Default item count
  - `CLAWFEEDRADAR_MAX_ITEMS` as the default when `--max-items` is omitted

### 3. Run a single HN frontpage (debug)

```bash
cd ~/.openclaw/workspace/clawfeedradar
python -m clawfeedradar.cli run   --root ~/.openclaw/workspace/orgmode/clawsqlite/data   --url https://hnrss.org/frontpage   --output ./feeds/hn-frontpage.xml   --score-threshold 0.4   --max-items 5   --source-lang en   --target-lang zh
```

This produces:

- `./feeds/hn-frontpage.xml`  
  RSS feed where `<description>` combines:
  - `summary_preview` (short summary in target language) and
  - `body_bilingual` (full bilingual body, screen-sized segments).
- `./feeds/hn-frontpage.json`  
  Sidecar JSON containing `fulltext`, `summary_preview`, `body_bilingual`, and scoring details.

---

## Configuration

See `ENV.example` for the full set of variables. The most important groups are:

- clawsqlite (`CLAWSQLITE_ROOT`, `CLAWSQLITE_DB`)
- Embedding (`EMBEDDING_*`, `CLAWSQLITE_VEC_DIM`)
- Radar output and scraping (`CLAWFEEDRADAR_OUTPUT_DIR`, `CLAWFEEDRADAR_SCRAPE_CMD`, `CLAWFEEDRADAR_SCRAPE_WORKERS`)
- Scoring weights and recency (`CLAWFEEDRADAR_W_*`, `CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS`)
- Small LLM (`SMALL_LLM_*`, `CLAWFEEDRADAR_LLM_*`)
- Default selection size (`CLAWFEEDRADAR_MAX_ITEMS`).

The interest score is computed as:

```text
interest = W_SIM_BEST   * sim_best
         + W_SIM_SECOND * border
         + W_RECENCY    * recency
         + W_POPULARITY * popularity
```

where `border = sim_second * (1 - sim_best)` favors items near the boundary of two clusters.

---

## Usage

### `clawfeedradar run`

Run the radar for a single source URL and write one XML+JSON pair.

```bash
python -m clawfeedradar.cli run   --root /path/to/knowledge_data   --url https://hnrss.org/frontpage   --output ./feeds/hn-frontpage.xml   --score-threshold 0.4   --max-items 5   --source-lang en   --target-lang zh
```

- `--root` overrides `CLAWSQLITE_ROOT`.
- `--url` is a single source URL (HN RSS / generic RSS).
- `--output` is the RSS XML path (default: `$CLAWFEEDRADAR_OUTPUT_DIR/radar.xml`).
- `--score-threshold` is the minimum `interest_score` to keep a candidate.
- `--max-items` caps the number of selected items; precedence: CLI > `CLAWFEEDRADAR_MAX_ITEMS` > default 12.
- `--source-lang` / `--target-lang` are hints for the small LLM.
- `--json` prints JSON to stdout in addition to writing the sidecar file.

### `clawfeedradar schedule`

Run multiple sources defined in `sources.json`, each producing `{label}.xml` + `{label}.json`.

Example `sources.json` entry:

```jsonc
[
  {
    "label": "hn-frontpage",
    "url": "https://hnrss.org/frontpage",
    "interval_hours": 8,
    "max_entries": 5,
    "score_threshold": 0.4,
    "source_lang": "en",
    "target_lang": "zh",
    "last_success_at": null,
    "last_error": null
  }
]
```

Command:

```bash
python -m clawfeedradar.cli schedule   --root /path/to/knowledge_data   --sources-json /path/to/sources.json   --output-dir /path/to/feeds
```

For each entry:

- If `last_success_at` is `null` or older than `interval_hours`, the source is due.
- The same scoring/selection pipeline as `run` is executed, with `max_items=max_entries` and `score_threshold` from the entry.
- Outputs `{label}.xml` + `{label}.json` into `output-dir`.
- Updates `last_success_at` / `last_error` in `sources.json`.

Convention:

- `score_threshold` is **only** configured per-source in `sources.json`, not in `.env`.
- `max_entries` is the per-source cap for scheduled runs and does not reuse `CLAWFEEDRADAR_MAX_ITEMS`.

---

## Behavior details

### Fulltext fetching concurrency

- `CLAWFEEDRADAR_SCRAPE_WORKERS` controls the thread pool size (e.g. 4).
- **Per-host serialization, cross-host parallelism**:
  - Each host has a lock; URLs with the same host run under `with lock:` and are effectively serialized.
  - Different hosts can be fetched in parallel, up to `SCRAPE_WORKERS` threads.
- Logs show URL/host distribution, e.g.:

```text
[pipeline] fulltext fetch: urls=100, hosts=1, max_workers=4
[pipeline] single host 'arxiv.org' detected; requests to this host are serialized via host-level locks
```

### Scoring and selection

- For each candidate:
  - Embed (using the shared embedding service).
  - Compute `sim_best` / `sim_second` against interest cluster centroids.
  - Compute `border = sim_second * (1 - sim_best)`.
  - Compute `recency` with an exponential decay using `RECENCY_HALF_LIFE_DAYS`.
  - Read `popularity_score` from the source adapter.
- Final interest score:

```text
interest = W_SIM_BEST   * sim_best
         + W_SIM_SECOND * border
         + W_RECENCY    * recency
         + W_POPULARITY * popularity_score
```

- Selection:
  - Filter candidates with `interest_score >= score_threshold`.
  - Sort by final score.
  - Take top `max_items`.
  - Only selected items go through LLM.
  - Logs the selection summary:

```text
[pipeline] scored=20, passed_threshold=15, max_items=5, selected=5 (score_threshold=0.400)
```

### LLM behavior

- Preview summary:
  - Input: a long summary (~1200 chars + last paragraph), **no extra truncation**.
  - Output: short summary in the target language, used at the top of RSS `<description>`.
- Bilingual body:
  - Input: fulltext.
  - Split by blank lines into paragraphs, then further split long paragraphs into screen-sized segments controlled by `CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS`.
  - For all segments of a single article, send them in character-budgeted chunks to the small LLM, up to 3 attempts with partial success.
  - Output: alternating original + translated segments.

### XML / JSON outputs

- `run`:
  - `--output foo.xml` → JSON sidecar at `foo.json`.
- `schedule`:
  - `label` → `label.xml` + `label.json`.
- JSON fields:
  - `fulltext`, `summary_preview`, `body_bilingual`, scoring details, etc.
- RSS `<description>`:

```text
description = summary_preview + "

" + body_bilingual
```

(with fallbacks if one of them is empty).

---

## Design

For deeper design rationale (interest clusters, scoring, source adapters), see:

- `docs/DESIGN.md`
- `docs/SPEC.md`

The v0 implementation keeps the core contract simple:

- clawfeedradar does not call into internal clawsqlite Python APIs; it only relies on the DB schema and external services (embedding/LLM/scraper).
- The main scoring channel is source-agnostic; source-specific behavior is minimal and explicit.
- Selection is top-N with a transparent formula; diversity is primarily driven by the `border` term instead of opaque explore slots.

---

## TODO

- [ ] More source adapters (HN API, arXiv API, etc.).
- [ ] Richer scoring diagnostics (per-cluster contributions, explanations).
- [ ] Feed-level explanations ("why this article").
- [ ] Split README into `README_zh.md` (Chinese) and `README.md` (English), with cross-links.
- [ ] Iterate scoring / LLM / scraping strategies based on real usage, then freeze a stable v1 spec.
