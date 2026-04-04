# clawfeedradar 规格说明（v1）

> 本文件描述 **当前实现版本**（基于 `bot/20260402-embedding` 分支）的核心规格。
> 老的 v0 设计（`W_SIM_BEST` / `border` / `sources.txt` 等）已经废弃，仅保留在 git 历史中，如需考古请查旧版本。

范围：

- 单用户、基于已有 `clawsqlite` 知识库（interest_clusters）
- 从 HN / RSS / 其它源抓取候选
- 基于兴趣簇 + 时间 + 热度打分
- 输出 per-source 的 RSS XML + JSON
- 中英/多语 LLM 摘要 + 对译（可关闭）

---

## 1. 总体目标

- 让 `clawsqlite` 维护的兴趣簇成为个人“兴趣空间”的唯一来源；
- clawfeedradar 仅负责：抓数据 → 统一候选模型 → 对照兴趣空间打分 → 输出可订阅 feed；
- 打分逻辑 **可解释**、**可调参**、**失败可恢复**，避免 pipeline 因为单点异常崩溃。


## 2. 与 clawsqlite 的边界

### 2.1 clawsqlite 侧（知识库 + 兴趣簇）

由 `clawsqlite knowledge build-interest-clusters` 负责：

- 从 `articles` / `articles_vec` / `articles_tag_vec` 读出文章向量；
- 使用环境变量 `CLAWSQLITE_INTEREST_TAG_WEIGHT`（默认 0.75）将摘要向量和标签向量线性混合：

  ```text
  v_interest = (1 - w_tag) * v_summary + w_tag * v_tag
  ```

- 基于混合向量做 k-means + 合并小簇，写入：
  - `interest_clusters(id, label, size, centroid, ...)`
  - `interest_cluster_members(cluster_id, article_id, ...)`
- 成功构建后，向 `interest_meta` 写入：
  - `key='interest_clusters_last_built_at', value=<UTC ISO8601>`

clawfeedradar 只读：

- `interest_clusters`（簇中心 + size）；
- `interest_meta`（用于提示簇是否过期）。

### 2.2 clawfeedradar 侧

职责：

1. 从外部源拉取候选，统一为 `Candidate`；
2. 把候选映射到 clawsqlite 兴趣空间，计算兴趣向量；
3. 对所有候选打分（兴趣主通道 + 时间/热度偏置 + 源特化通道）；
4. 选出 top-N，抓全文 + LLM 摘要/对译，输出 XML + JSON。

clawfeedradar **不修改** clawsqlite DB schema，只依赖：

- `CLAWSQLITE_ROOT` / `CLAWSQLITE_DB`；
- `EMBEDDING_*` / `CLAWSQLITE_VEC_DIM`；
- `interest_clusters` / `interest_meta`。


## 3. 数据模型

### 3.1 Candidate（候选文章）

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
    popularity_score: float  # 0..1，源适配器归一化
    source_meta: dict[str, Any]
```

约定：

- `popularity_score`：仅在源有真实信号时赋值（如 HN points/comments）；
  - 对普通 RSS 源，默认 0.0（不再伪造 0.5 中性值）。
- `source_meta`：仅供源特化通道使用，主打分逻辑只看 `popularity_score`。

### 3.2 ClusterInfo / InterestMatch / ScoredItem

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
    interest_score: float        # sigmoid 拉伸后的兴趣分
    interest_score_raw: float    # 线性兴趣分（簇加权相似度）
    final_score: float
    match: InterestMatch
```


## 4. 环境变量（v1）

这里只列出与核心逻辑相关的变量，完整版见 `ENV.example`。

### 4.1 clawsqlite + Embedding

```env
CLAWSQLITE_ROOT=/path/to/clawsqlite/data
CLAWSQLITE_DB=/path/to/clawkb.sqlite3

EMBEDDING_BASE_URL=...
EMBEDDING_MODEL=...
EMBEDDING_API_KEY=...
CLAWSQLITE_VEC_DIM=1024

CLAWSQLITE_INTEREST_TAG_WEIGHT=0.75   # 0..1，控制 summary/tag 向量混合权重
```

### 4.2 抓取与输出

```env
CLAWFEEDRADAR_OUTPUT_DIR=/path/to/feeds
CLAWFEEDRADAR_SCRAPE_CMD="/path/to/clawfetch_wrapper.sh"
CLAWFEEDRADAR_SCRAPE_WORKERS=4
# CLAWFEEDRADAR_HTTP_USER_AGENT=...

CLAWFEEDRADAR_MAX_ITEMS=12   # run 模式默认选几条（可被 --max-items 覆盖）
```

抓取策略：

- 使用 `ThreadPoolExecutor(max_workers=CLAWFEEDRADAR_SCRAPE_WORKERS)`；
- 以 `urlparse(url).netloc` 为 key 做 host 级锁：
  - 同一 host 下请求串行；
  - 跨 host 并行；
- 抓取失败按既有 `fetch_fulltext` 语义重试（3 次 + 退避）。

### 4.3 打分参数

```env
CLAWFEEDRADAR_W_RECENCY=0.05
CLAWFEEDRADAR_W_POPULARITY=0.05
CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS=3

# S 型 sigmoid 拉伸的陡峭程度（越大越接近 0/1）
CLAWFEEDRADAR_INTEREST_SIGMOID_K=4.0

# HN / arxiv 源特化通道的权重
CLAWFEEDRADAR_LAMBDA_HN=0.2
CLAWFEEDRADAR_LAMBDA_ARXIV=0.1
```

### 4.4 LLM 相关

```env
SMALL_LLM_BASE_URL=...
SMALL_LLM_MODEL=...
SMALL_LLM_API_KEY=...

# 上下文预算：近似 token 上限，由代码内部乘以 ~4 转换为字符预算
CLAWFEEDRADAR_LLM_CONTEXT_TOKENS=8096

# 对译段落级 "一屏" 长度（字符）
CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS=2400

# TAG 数量提示
CLAWFEEDRADAR_LLM_TAG_MAX_PER_ITEM=12

# LLM 请求节流
CLAWFEEDRADAR_LLM_SLEEP_BETWEEN_MS=500

# 语言偏好
CLAWFEEDRADAR_LLM_SOURCE_LANG=auto
CLAWFEEDRADAR_LLM_TARGET_LANG=zh
```


## 5. 源适配器

统一入口：

```python
def fetch_candidates_from_source(source_type: str, source_url: str, max_items: int | None = None) -> list[Candidate]:
    ...
```

### 5.1 RSS 适配器（sources/rss.py）

- 使用 `feedparser.parse(source_url)` 获得 entries；
- 从 entry 提取：`link` / `title` / `summary/description` / `tags` / `published_at`；
- 源类型：
  - 如果 feed URL host 包含 `hnrss.org` 或 `news.ycombinator.com` → `source="hackernews"`；
  - 如果 entry 链接 host 包含 `arxiv.org` → `source="arxiv"`；
  - 否则 `source="rss"`。
- popularity：

  ```python
  pop = 0.0
  if source == "hackernews":
      # 从标题/summary 中解析 "123 points | 45 comments"
      # 归一化到 0..1 填入 popularity_score
  ```

### 5.2 HN / arxiv 专用适配器

当前 v1 中，

- HN RSS 也走 `rss` 适配器，只在 popularity_score 和 source_meta 中保留 points/comments；
- 如需 arxiv 专用逻辑，可后续新增 `sources/arxiv.py`，但必须遵守 Candidate 模型。


## 6. 打分与排序

### 6.1 兴趣向量构造

对每个过滤后的候选：

1. 使用 `_build_long_summary(fulltext)` 构造长摘要：
   - 按空行分段；
   - 从头累加段落，直到接近约 1200 字符，在段落边界截断；
   - 若末段未包含，则追加末段；
   - 若全文抓取失败，则回退为 `title + "\n\n" + summary`。
2. 对所有长摘要批量调用 LLM 生成 TAG（`generate_tags_bulk`，支持部分成功 + 重试）。
3. 使用 embedding 客户端：
   - `embed_texts(long_summaries)`：串行、带重试和限速；
   - 对 TAG 文本调用 `embed_text`，失败降级为零向量。
4. 按 `CLAWSQLITE_INTEREST_TAG_WEIGHT` 混合 summary/tag embedding，得到兴趣向量：

   ```text
   interest_emb = (1 - w_tag) * summary_emb + w_tag * tag_emb
   ```

### 6.2 interest_score 计算

1. 从 clawsqlite 读取所有兴趣簇：`ClusterInfo(id, size, centroid)`；
2. 预先计算簇权重：

   ```text
   total_size = Σ_k max(1, size_k)
   cluster_weight_k = size_k / total_size
   ```

3. 对每个候选兴趣向量 `emb`：

   ```python
   # 线性兴趣分
   interest_raw = 0.0
   best_cluster_id = -1
   best_sim = 0.0
   second_sim = 0.0

   for cluster in clusters:
       sim = cosine(emb, cluster.centroid)
       sim = max(sim, 0.0)  # 负值截断为 0
       w = cluster_weight[cluster.id]
       interest_raw += w * sim

       # 跟踪 top-2 相似度
       if sim > best_sim:
           second_sim = best_sim
           best_sim = sim
           best_cluster_id = cluster.id
       elif sim > second_sim:
           second_sim = sim

   best_cluster_weight = cluster_weight[best_cluster_id]
   ```

4. S 型 sigmoid 拉伸：

   ```python
   # k 来自 CLAWFEEDRADAR_INTEREST_SIGMOID_K，默认 4.0
   z = k * (interest_raw - 0.5)
   interest = 1.0 / (1.0 + exp(-z))
   ```

   - `interest_raw` 与 `interest` 均写入 `ScoredItem`；
   - JSON 输出中字段为 `interest_score_raw` / `interest_score`。

### 6.3 时间与热度偏置

在 sigmoid 后的 `interest` 上叠加轻度偏置：

```python
rec = recency_weight(published_at, now, half_life_seconds)
# half_life_seconds = CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS * 24 * 3600

pop = clamp(candidate.popularity_score, 0.0, 1.0)

interest_bias = params.w_recency * rec + params.w_popularity * pop
biased_interest = interest + interest_bias
```

默认：

- `w_recency = w_popularity = 0.05`；
- 对无 popularity 信号的源，`pop=0`，不会产生伪造偏置。

### 6.4 源特化通道与 final_score

源特化通道只允许 *加分*，并且通过一个 λ 系数稀释：

```python
def score_generic_extra(cand: Candidate, base: float) -> float:
    return 0.0


def score_hn_extra(cand: Candidate, base: float) -> float:
    meta = cand.source_meta or {}
    points = float(meta.get("hn_points", 0) or 0)
    comments = float(meta.get("hn_comments", 0) or 0)
    s_points = min(1.0, points / 500.0)
    s_comments = min(1.0, comments / 100.0)
    return 0.5 * s_points + 0.5 * s_comments


SOURCE_SCORERS = {
    "hackernews": score_hn_extra,
    "arxiv": score_arxiv_extra,   # 目前返回 0.0
}

LAMBDA_SOURCE = {
    "hackernews": λ_hn,   # 默认 0.2
    "arxiv": λ_arxiv,     # 默认 0.1
    "default": 0.1,
}


def compute_final_score(cand: Candidate, interest_score: float) -> float:
    extra_fn = SOURCE_SCORERS.get(cand.source, score_generic_extra)
    lam = LAMBDA_SOURCE.get(cand.source, LAMBDA_SOURCE["default"])
    extra = float(extra_fn(cand, interest_score) or 0.0)
    return interest_score + lam * extra
```

最终 `ScoredItem.final_score` 用于排序，JSON 中写入 `final_score`。


## 7. 选择与输出

### 7.1 选择策略

1. 对所有候选得到 `ScoredItem` 列表；
2. 按 `final_score` 降序排序；
3. 过滤：`interest_score >= score_threshold`；
4. 取前 `max_items` 条。

v1 中暂不做复杂的“按簇配额 + 探索条目”算法，后续如需要可在此处扩展（例如按 `best_cluster_id` 分桶做轮询）。

### 7.2 JSON sidecar

对每个 run/schedule 输出的 JSON，单条结构示例：

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


## 8. CLI 与调度

### 8.1 `clawfeedradar run`

- 单源模式：

  ```bash
  clawfeedradar run \
    --root /path/to/knowledge_data \
    --url https://example.com/feed.xml \
    --output ./feeds/example.xml \
    --score-threshold 0.4 \
    --max-items 12 \
    [--max-source-items 50] \
    [--w-recency 0.05] [--w-popularity 0.05] \
    [--feed-title "My Radar"] \
    [--source-lang en] [--target-lang zh] \
    [--no-preview] [--preview-words 512] \
    [--json]
  ```

- CLI 优先级：
  - `--root` > `CLAWSQLITE_ROOT`；
  - `--max-items` > `CLAWFEEDRADAR_MAX_ITEMS` > 默认 12；
  - `--w-recency` / `--w-popularity` > 环境变量 > 默认值。

### 8.2 `clawfeedradar schedule`

- 从 `sources.json` 读取多个源，每个源输出 `{label}.xml` + `{label}.json`：

  ```bash
  clawfeedradar schedule \
    --root /path/to/knowledge_data \
    --sources-json /path/to/sources.json \
    --output-dir ./feeds
  ```

- `sources.json` 单条示例：

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

- 调度逻辑：
  - `last_success_at` 为空或早于 `now - interval_hours` → 判定为 due；
  - 运行与 `run` 相同的 pipeline，`max_items = max_entries`；
  - 更新 `last_success_at` / `last_error` 写回 `sources.json`。

---

本 SPEC 以 clawfeedradar 当前实现为准。
未来若有新的 scoring/LLM 行为引入，应同步更新本文件，避免 v0/v1 设计混在一起。
