# clawfeedradar

个人「读报雷达」：围绕 `clawsqlite` 知识库，从 Hacker News / RSS / arXiv 等信息源中筛选**你可能感兴趣**的文章，自动生成带中英对照摘要的专属 RSS feed。

> 设计目标：简单可控、行为可审计、和 `clawsqlite` 解耦。本文档描述的是 **当前 v1 实现**（分支 `bot/20260402-embedding`）。

---

## 简介

clawfeedradar 的核心工作是三件事：

1. **从外部源抓候选文章**  
   支持 HN RSS、普通 RSS，后续可扩展 arXiv 等。
2. **利用 clawsqlite 的兴趣簇做打分排序**  
   通过 Embedding + 兴趣簇，算出每篇候选文章和「你的长期兴趣」的匹配度，并叠加少量时间/热度偏置。
3. **生成带中英对照摘要的 RSS feed**  
   对选中的文章抓全文，调用小 LLM 生成预览摘要 + 中英对照正文，并写出一对 XML（给 RSS 阅读器）+ JSON（调试/复用）。

一句话：**clawsqlite 负责“知道你喜欢什么”，clawfeedradar 负责“去外面帮你找类似的东西，翻译好喂给你的 RSS 阅读器”。**

---

## 快速开始

### 1. 准备环境

假设你的目录结构类似：

```text
~/.openclaw/workspace/
  ├── clawsqlite          # clawsqlite 仓库（已有）
  ├── knowledge_data      # clawsqlite-knowledge 知识库
  ├── clawfeedradar       # 本仓库
  └── clawfetch / ...     # 抓全文用的 clawfetch + wrapper
```

先确保 clawsqlite 这边已经有一个兴趣簇：

```bash
cd ~/.openclaw/workspace/clawsqlite
clawsqlite knowledge build-interest-clusters \
  --root ~/.openclaw/workspace/knowledge_data
```

### 2. 配置 clawfeedradar

```bash
cd ~/.openclaw/workspace/clawfeedradar
cp ENV.example .env
# 打开 .env 按你的环境改一遍
```

最小可工作配置（核心几类）：

- clawsqlite 知识库
  - `CLAWSQLITE_ROOT` 指向 `knowledge_data` 根目录
  - 如需要显式指定 sqlite 路径，可配置 `CLAWSQLITE_DB`
- Embedding 服务（与 clawsqlite 共用）
  - `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_API_KEY`
  - `CLAWSQLITE_VEC_DIM` 必须与 embedding 模型维度一致
- 输出目录
  - `CLAWFEEDRADAR_OUTPUT_DIR`：生成的 XML/JSON 放在哪个目录
- 抓全文
  - `CLAWFEEDRADAR_SCRAPE_CMD`：接受 URL、输出 markdown 的命令，通常是调用 `clawfetch` 的 wrapper
  - `CLAWFEEDRADAR_SCRAPE_WORKERS`：抓取并发 worker 数（同 host 内仍串行）
- 小 LLM（摘要 + 中英对照）
  - `SMALL_LLM_BASE_URL` / `SMALL_LLM_MODEL` / `SMALL_LLM_API_KEY`
  - `CLAWFEEDRADAR_LLM_CONTEXT_TOKENS`：近似 token 上限，代码内部会乘以 ~4 转换为字符预算
  - `CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS`：每个中英对照段的最大字符数（按屏拆分），例如 2400 ≈ 600 字
  - `CLAWFEEDRADAR_LLM_SLEEP_BETWEEN_MS`：两次调用之间的 sleep（毫秒）
  - `CLAWFEEDRADAR_LLM_SOURCE_LANG` / `CLAWFEEDRADAR_LLM_TARGET_LANG`
- 打分权重
  - `CLAWFEEDRADAR_W_RECENCY`
  - `CLAWFEEDRADAR_W_POPULARITY`
  - `CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS`（recency 半衰期，以天为单位）
  - `CLAWFEEDRADAR_INTEREST_SIGMOID_K`：S 型 sigmoid 在 0.5 附近的陡峭程度
- 默认条目数
  - `CLAWFEEDRADAR_MAX_ITEMS`：不写 `--max-items` 时，每轮最多选几条

### 3. 跑一轮 BBC Tech（单源调试）

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

跑完后你会得到：

- `./feeds/bbc-tech.xml`  
  一个 RSS feed，适合直接在 RSS 阅读器里订阅；每条 `<description>` 包含：
  - 预览摘要（`summary_preview`，目标语言）
  - 全文中英对照正文（`body_bilingual`，按屏拆分）
- `./feeds/bbc-tech.json`  
  同名 JSON sidecar，包含：
  - 原始 `fulltext`
  - `summary_preview` / `body_bilingual`
  - 打分明细：`interest_score` / `interest_score_raw` / `final_score` / `best_cluster_id` / `best_cluster_weight` 等

---

## 配置概览

完整列表参见 `ENV.example`，这里只强调主要配置：

- clawsqlite：`CLAWSQLITE_ROOT` / `CLAWSQLITE_DB`
- Embedding：`EMBEDDING_*` / `CLAWSQLITE_VEC_DIM` / `CLAWSQLITE_INTEREST_TAG_WEIGHT`
- 输出与抓取：`CLAWFEEDRADAR_OUTPUT_DIR` / `CLAWFEEDRADAR_SCRAPE_CMD` / `CLAWFEEDRADAR_SCRAPE_WORKERS`
- 打分：`CLAWFEEDRADAR_W_RECENCY` / `CLAWFEEDRADAR_W_POPULARITY` / `CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS` / `CLAWFEEDRADAR_INTEREST_SIGMOID_K`
- 小 LLM：`SMALL_LLM_*` / `CLAWFEEDRADAR_LLM_CONTEXT_TOKENS` / `CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS` / `CLAWFEEDRADAR_LLM_TAG_MAX_PER_ITEM` / `CLAWFEEDRADAR_LLM_SLEEP_BETWEEN_MS` / `CLAWFEEDRADAR_LLM_SOURCE_LANG` / `CLAWFEEDRADAR_LLM_TARGET_LANG`
- 默认条数：`CLAWFEEDRADAR_MAX_ITEMS`

---

## 打分（当前 v1）

### 兴趣向量构造

对每个候选：

1. 抓全文（带 host 级锁，跨 host 并行，失败重试）。
2. 使用 `_build_long_summary(fulltext)` 构造长摘要：
   - 按空行切成段落；
   - 从头开始累加段落，直到接近约 1200 字符，在段落边界截断；
   - 若最后一段未包含，则追加末段；
   - 若全文抓取失败，则回退为 `title + "\n\n" + summary`。
3. 对所有长摘要批量调用小 LLM 生成 TAG（`generate_tags_bulk`），支持部分成功 + 重试。
4. 使用串行 embedding 客户端，对长摘要和 TAG 分别做 embedding（带重试和限速）。
5. 根据 `CLAWSQLITE_INTEREST_TAG_WEIGHT` 混合 summary/tag embedding，得到兴趣向量。

### interest_score

1. 从 clawsqlite 读取所有兴趣簇：`ClusterInfo(id, label, size, centroid)`。
2. 预先计算簇权重：

   ```text
   total_size = Σ_k max(1, size_k)
   cluster_weight_k = size_k / total_size
   ```

3. 对每个候选兴趣向量 `emb`：

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

4. 使用以 0.5 为中心的 S 型 sigmoid 做拉伸：

   ```python
   # k 来自 CLAWFEEDRADAR_INTEREST_SIGMOID_K（默认 4.0）
   z = k * (interest_raw - 0.5)
   interest = 1.0 / (1.0 + exp(-z))
   ```

JSON 中：

- `interest_score_raw`：线性兴趣分（簇加权相似度）；
- `interest_score`：sigmoid 拉伸后的兴趣分（推荐用来设阈值）。

### 时间与热度偏置

在 `interest` 之上叠加轻度偏置：

```python
rec = recency_weight(published_at, now, half_life_seconds)
# half_life_seconds = CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS * 24 * 3600

pop = clamp(candidate.popularity_score, 0.0, 1.0)

interest_bias = w_recency * rec + w_popularity * pop
biased_interest = interest + interest_bias
```

默认：

- `w_recency = w_popularity = 0.05`；
- 对普通 RSS 源，`popularity_score` 默认 0.0（不再伪造 0.5 中性值）。

通过 CLI 可 per-run 覆盖：`--w-recency` / `--w-popularity`。

### 源特化通道与 final_score

对少数头部源（如 HN）允许轻度源特化通道：

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
    "arxiv": score_arxiv_extra,   # 当前返回 0.0
}

LAMBDA_SOURCE = {
    "hackernews": λ_hn,   # 默认 0.2
    "arxiv": λ_arxiv,     # 默认 0.1
    "default": 0.1,
}


def compute_final_score(cand, interest_score):
    extra_fn = SOURCE_SCORERS.get(cand.source, score_generic_extra)
    lam = LAMBDA_SOURCE.get(cand.source, LAMBDA_SOURCE["default"])
    extra = float(extra_fn(cand, interest_score) or 0.0)
    return interest_score + lam * extra
```

最终用于排序的是 `final_score`。

---

## CLI

### `clawfeedradar run` — 单源模式

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

- `--root` 覆盖 `CLAWSQLITE_ROOT`；
- `--max-items` 覆盖 `CLAWFEEDRANDAR_MAX_ITEMS`；
- `--max-source-items` 控制打分前从源 feed 拉取的最大 entry 数；
- `--w-recency` / `--w-popularity` 覆盖当前 run 的偏置权重；
- `--feed-title` 设置本次 RSS `<title>`；
- `--no-preview` 关闭预览摘要 LLM 调用，只跑抓取 + 打分；
- `--preview-words` 以 **词数** 控制预览摘要长度（而不是字符数）；
- `--json` 额外在 stdout 打印 JSON。

### `clawfeedradar schedule` — 多源调度

```bash
clawfeedradar schedule \
  --root /path/to/knowledge_data \
  --sources-json /path/to/sources.json \
  --output-dir ./feeds
```

`sources.json` 每条示例：

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

行为：

- 对于 due 的源（`last_success_at` 为空或早于 `now - interval_hours`）：
  - 运行与 `run` 相同的 pipeline，`max_items = max_entries`；
  - 输出 `{label}.xml` + `{label}.json` 到 `--output-dir`；
  - 更新该条目的 `last_success_at` / `last_error`。

---

更详细的设计/规格见 `docs/SPEC.md` / `docs/DESIGN.md`。