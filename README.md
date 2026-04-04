# clawfeedradar

Personal "reading radar" built on top of `clawsqlite`: it pulls articles from Hacker News / RSS / arXiv and ranks them against your existing knowledge base, generating a personalized RSS feed with optional bilingual summaries.

> Goal: simple, controllable, auditable, and loosely coupled with `clawsqlite`. This document describes the **current v1 implementation** (branch `bot/20260402-embedding`).

---

## When running inside OpenClaw

clawfeedradar expects an existing `clawsqlite` knowledge base with interest clusters.

In an OpenClaw workspace, the recommended way to set this up is:

1. **Install the `clawsqlite-knowledge` skill** (if not already installed):

   ```bash
   openclaw skills add clawsqlite-knowledge
   ```

   Or via the web catalog:

   - <https://clawhub.ai/skills/clawsqlite-knowledge>

2. **Initialize and build interest clusters** using that skill (see the skill README for the exact commands).

Once `clawsqlite-knowledge` is installed and has built `interest_clusters`, clawfeedradar can attach to the same DB and reuse the interest space for scoring.

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

## Publishing feeds via Git (GitHub Pages / Gitee Pages)

clawfeedradar can optionally push the generated XML/JSON to a git repository,
so that GitHub Pages or Gitee Pages can host your feed at a stable HTTPS URL.

### 1) GitHub Pages example

1. 创建一个公开仓库，例如：`github.com/yourname/clawfeedradar-feed`，在 Settings → Pages 中
   启用 Pages 功能，选择：

   - Branch: `gh-pages`
   - Directory: `/` 或 `feeds/`（下面示例使用 `feeds/`）

2. 在运行 clawfeedradar 的机器上，配置好 git 访问 GitHub 的方式（SSH key 或 HTTPS+PAT）。

3. 在 `.env` 中添加：

   ```env
   CLAWFEEDRADAR_PUBLISH_GIT_REPO=git@github.com:yourname/clawfeedradar-feed.git
   CLAWFEEDRADAR_PUBLISH_GIT_BRANCH=gh-pages
   CLAWFEEDRADAR_PUBLISH_GIT_PATH=feeds
   ```

4. 之后每次运行 `clawfeedradar run` / `schedule`：

   - clawfeedradar 会在本地 `./.publish/yourname-clawfeedradar-feed/` 下维护一个 clone；
   - 将生成的 `*.xml` / `*.json` 拷贝到该 clone 的 `feeds/` 目录；
   - 自动执行 `git add` / `git commit` / `git push`。

5. 最终订阅地址类似于：

   ```text
   https://yourname.github.io/clawfeedradar-feed/feeds/bbc-tech.xml
   ```

### 2) Gitee Pages example（适合国内网络）

步骤与 GitHub 类似，只是把远端换成 Gitee：

1. 创建 Gitee 仓库，例如：`gitee.com/yourname/clawfeedradar-feed`，在 Pages 设置中
   启用 Gitee Pages（选择对应分支和目录）。

2. 在运行环境中配置好访问 `git@gitee.com:...` 的 SSH key。

3. 在 `.env` 中添加：

   ```env
   CLAWFEEDRADAR_PUBLISH_GIT_REPO=git@gitee.com:yourname/clawfeedradar-feed.git
   CLAWFEEDRADAR_PUBLISH_GIT_BRANCH=gh-pages
   CLAWFEEDRADAR_PUBLISH_GIT_PATH=feeds
   ```

4. 之后 clawfeedradar 的行为与 GitHub 情况一致：每次生成 XML/JSON 后自动
   `git add` / `commit` / `push` 到 Gitee 仓库。

5. Gitee Pages 的订阅地址通常类似：

   ```text
   https://yourname.gitee.io/clawfeedradar-feed/feeds/bbc-tech.xml
   ```

如未配置 `CLAWFEEDRADAR_PUBLISH_GIT_REPO`，clawfeedradar 仅在本地写 XML/JSON，
不会尝试推送任何远端仓库。

---

## Configuration (env overview)

（其余章节与之前相同，略）
