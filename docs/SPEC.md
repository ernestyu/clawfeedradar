# clawfeedradar 规格说明（v0）

本文件将当前讨论达成一致的设计固化为更偏“规格”的形式，供后续实现与 review 使用。

> 范围：仅覆盖 v0（单用户、基于已有 clawsqlite-knowledge 知识库、输出 RSS/XML+JSON）。

---

## 1. 目标与范围

### 1.1 目标

- 基于现有 `clawsqlite-knowledge` 知识库，构建个人“兴趣空间”。
- 定期从多个信息源（Hacker News / arXiv / RSS / 其它）抓取文章候选。
- 对候选进行**兴趣打分 + 多样性控制 + 适度探索**，生成可订阅的技术/论文“读报” feed：
  - 输出 RSS/Atom XML（供 RSS 阅读器订阅）；
  - 同时输出一份 JSON（便于调试或其它应用复用）。

### 1.2 非目标

- 不做多用户推荐系统，所有逻辑针对“单用户 + 单知识库”。
- 不在 clawfeedradar 内重复实现整套向量管理/聚类逻辑：
  - Embedding / vec 表 / 兴趣簇维护由 `clawsqlite` 提供；
  - 雷达只消费这些信息做打分与排序。

---

## 2. 系统分层与责任边界

### 2.1 clawsqlite 侧（知识库 + 兴趣簇）

主职责：**维护知识库 + 向量 + 兴趣簇元数据**。

当前设计/实现（分支 `bot/20260328-interest-clusters`）：

1. 向量表：
   - `articles_vec(id, embedding)`：摘要向量（summary embedding）；
   - `articles_tag_vec(id, embedding)`：标签向量（tags embedding）。

2. 兴趣簇聚类命令：

   ```bash
   clawsqlite knowledge build-interest-clusters \
     --root /path/to/knowledge_data \
     --min-size 5 \
     --max-clusters 64 \
     [--json]
   ```

   行为：

   - 从 `articles` / `articles_vec` / `articles_tag_vec` 中选取候选：

     ```sql
     SELECT a.id AS id,
            sv.embedding AS summary_embedding,
            tv.embedding AS tag_embedding
     FROM articles a
     LEFT JOIN articles_vec sv      ON sv.id = a.id
     LEFT JOIN articles_tag_vec tv  ON tv.id = a.id
     WHERE a.deleted_at IS NULL
       AND a.summary IS NOT NULL AND trim(a.summary) != ''
     ```

   - 将 summary/tag 向量按权重混合成“兴趣向量”：

     ```text
     v_interest = w_sum * summary_vec + w_tag * tag_vec
     ```

     其中：

     - `w_tag` 从环境变量 `CLAWSQLITE_INTEREST_TAG_WEIGHT` 读取，默认值为 `0.75`；
     - `w_tag` 被 clamp 在 `[0,1]`；
     - `w_sum = 1 - w_tag`；
     - 若仅有其中一个向量存在，则直接使用该向量作为 `v_interest`。

   - 使用纯 Python k-means 对 `v_interest` 做聚类：
     - 不引入 numpy / sklearn 等三方依赖；
     - 点集规模假定在“几百到几千”级别；
   - 聚类粒度由 `min_size` / `max_clusters` 控制：
     - `max_by_size = n // min_size` 给出在最小簇大小约束下的最大簇数；
     - 实际 `k = min(max_clusters, max_by_size)`，特殊情况退化为 `k=1`；
     - 将簇大小 < `min_size` 的“小簇”成员重分配到最近的大簇中心中；
     - 最终每个兴趣簇至少有 `min_size` 条样本（除非全体样本不足）。

   - 输出表：
     - `interest_clusters`：

       ```sql
       CREATE TABLE IF NOT EXISTS interest_clusters (
         id INTEGER PRIMARY KEY,
         label TEXT,
         size INTEGER NOT NULL,
         summary_centroid BLOB NOT NULL,
         created_at TEXT NOT NULL,
         updated_at TEXT NOT NULL
       );
       ```

       - `summary_centroid` 存储兴趣簇中心向量（使用 vec_dim 的 float32 blob，与 Embedding 一致）。

     - `interest_cluster_members`：

       ```sql
       CREATE TABLE IF NOT EXISTS interest_cluster_members (
         cluster_id INTEGER NOT NULL,
         article_id INTEGER NOT NULL,
         membership REAL NOT NULL,
         PRIMARY KEY (cluster_id, article_id)
       );
       ```

       - v0 中 `membership` 固定为 1.0，保留未来编码距离/权重的空间。

> `build-interest-clusters` 属于可选能力：只有显式调用时才会创建 interest_* 表，不影响已有 ingest/search 行为。

### 2.2 clawfeedradar 侧（本项目）

主职责：**数据源拉取 + 候选统一化 + 打分 + 输出 RSS/XML+JSON**。

- 只读 `knowledge.sqlite3`：
  - `interest_clusters` / `interest_cluster_members`；
  - 必要时可读 `articles` / `articles_vec` / `articles_tag_vec` 作补充；
- 不修改 DB schema；
- 自行管理：
  - 源列表文件；
  - 输出 feed 文件（XML + JSON）；
  - 打分/排序逻辑和源适配器。

---

## 3. 数据模型

### 3.1 Candidate（候选文章）

统一的内部结构：

```python
@dataclass
class Candidate:
    id: str             # 源内唯一 ID，如 hn-123456 / arxiv-xyz / rss-abc
    url: str
    title: str
    summary: str        # 抓取的正文片段或简短摘要
    tags: str           # 逗号分隔关键词，可为空
    source: str         # "hackernews" | "arxiv" | "rss" | ...
    published_at: datetime

    popularity_score: float         # 0..1，各源自行归一化后的“热度/重要性”

    source_meta: dict[str, Any]     # 源特定字段，仅供源特化打分使用
```

约束：

- 主打分逻辑不直接读取 `source_meta` 里的字段，仅用于源特化通道；
- `popularity_score` 是源方 adapter 的责任：将 HN points / arxiv signal 等归一化成 0..1。

### 3.2 ClusterInfo / InterestMatch

从 `interest_clusters` 读取：

```python
@dataclass
class ClusterInfo:
    id: int
    label: str
    size: int
    centroid: list[float]   # 从 summary_centroid blob 解码
```

候选 vs 簇匹配结果：

```python
@dataclass
class InterestMatch:
    best_cluster_id: int
    sim_best: float
    sim_second: float
```

---

## 4. 配置与环境变量

### 4.1 环境变量（clawfeedradar）

需要在本仓库提供 `ENV.example`，典型内容：

```env
# Embedding service (shared with clawsqlite)
EMBEDDING_BASE_URL=https://embed.example.com/v1
EMBEDDING_MODEL=your-embedding-model
EMBEDDING_API_KEY=sk-your-embedding-key
CLAWSQLITE_VEC_DIM=1024

# Knowledge base root used by clawsqlite
CLAWSQLITE_ROOT=/home/node/.openclaw/workspace/knowledge_data
# Optional override
# CLAWSQLITE_DB=/home/node/.openclaw/workspace/knowledge_data/knowledge.sqlite3

# Source list for clawfeedradar (one URL or identifier per line)
CLAWFEEDRADAR_SOURCES_FILE=/home/node/.openclaw/workspace/clawfeedradar/sources.txt

# Output dir for feeds
CLAWFEEDRADAR_OUTPUT_DIR=/home/node/.openclaw/workspace/clawfeedradar/feeds

# Optional: small LLM for summaries/translation
# SMALL_LLM_BASE_URL=https://llm.example.com/v1
# SMALL_LLM_MODEL=your-small-llm
# SMALL_LLM_API_KEY=sk-your-small-llm-key

# Approximate context limits (used by clawfeedradar when chunking fulltext).
# These are expressed in characters for simplicity; implementations may
# convert them to tokens internally if needed.
# 最大输入长度：每次传给小模型的源文本最大字符数。
CLAWFEEDRADAR_LLM_MAX_INPUT_CHARS=6000
# 预期输出长度：便于规划 prompt 和输出预算（不强制截断，以模型端为准）。
CLAWFEEDRADAR_LLM_MAX_OUTPUT_CHARS=6000

# Rate limiting / concurrency for LLM calls.
# v0: 所有翻译调用串行执行（不并行）。
# 如需在自建小模型上加额外保护，可设定两次调用之间的 sleep 间隔（毫秒）。
CLAWFEEDRADAR_LLM_SLEEP_BETWEEN_MS=500

# Language preferences for translation.
# 例如："en,zh" 表示输出中英对照；未来可扩展为多语言。
CLAWFEEDRADAR_LLM_TARGET_LANGS=en,zh

# External scraper command for fulltext fetch (recommended: clawfetch wrapper)
# The command should accept a URL and emit markdown/plaintext to stdout.
# Example (pseudo): CLAWFEEDRADAR_SCRAPE_CMD="clawfetch --url"
# or a shell wrapper that internally 调用 clawfetch skill。
# CLAWFEEDRADAR_SCRAPE_CMD=clawfetch --url

# Interest mixing weight (used by clawsqlite build-interest-clusters)
# CLAWSQLITE_INTEREST_TAG_WEIGHT=0.75

# Optional: GitHub Pages publishing config (see 7.2)
# CLAWFEEDRADAR_GITHUB_REPO=ernestyu/clawfeedradar-feed
# CLAWFEEDRADAR_GITHUB_BRANCH=gh-pages
# CLAWFEEDRADAR_GITHUB_FEED_PATH=feeds/radar.xml
```

### 4.2 源列表文件 `sources.txt`

格式：一行一个源 URL 或标识：

```text
https://news.ycombinator.com/
https://arxiv.org/list/cs.LG/recent
https://example.com/feed.xml
```

源类型自动识别：

- host 包含 `news.ycombinator.com` → `source="hackernews"`；
- host 包含 `arxiv.org` → `source="arxiv"`；
- 其它 → `source="rss"`；

源适配器负责从 URL 解析出更细粒度的参数（例如 RSS feed 名称、arxiv category 等）。

---

## 5. 组件与接口

### 5.1 源适配器（sources/*）

接口定义：

```python
def fetch_candidates_from_source(source_type: str, source_url: str) -> list[Candidate]:
    """拉取单个源的候选，并转成 Candidate 列表。"""
```

内建 adapter：

- `sources/hn.py`：
  - 拉取 HN top/new stories（官方 API）；
  - 提取 title/url/time/points/comment_count；
  - 归一化得到 `popularity_score`（例如基于 points+comments）；
  - 其它字段放入 `source_meta`。

- `sources/arxiv.py`：
  - 拉取 arxiv recent 列表（RSS/API）；
  - 提取 title/abstract/subject/category/date；
  - 简单规则生成 `popularity_score`（例如近期论文设为较高，历史长文设为中等）。

- `sources/rss.py`：
  - 使用 feedparser 等库解析任意 RSS/Atom；
  - 提取基本字段，`popularity_score` 默认中性（可后续加简单规则）。

### 5.2 兴趣簇读取（sqlite_interest）

职责：

1. 打开 `knowledge.sqlite3`；
2. 读取 `interest_clusters`，构建 `ClusterInfo` 列表；
3. 提供相似度计算函数：

```python
def score_against_clusters(emb: list[float], clusters: list[ClusterInfo]) -> InterestMatch:
    """计算候选向量与各兴趣簇中心的相似度，返回最相近簇信息。"""
```

- 相似度指标可用余弦或归一化后的 L2 距离；
- 需要提供 `sim_best` 和 `sim_second`，用于后续打分。

### 5.3 候选向量生成（Embedding）

雷达端使用与 clawsqlite 相同的 Embedding 服务：

```python
def embed_candidate(candidate: Candidate) -> list[float]:
    """对候选文章生成向量，默认使用 title+summary 作为输入文本。"""
```

- 使用 `EMBEDDING_*` 和 `CLAWSQLITE_VEC_DIM` 配置；
- 不依赖 clawsqlite 内部 embed 模块（保持项目解耦）。

---

## 6. 打分与排序

### 6.1 通用主通道：Interest Score

对每个候选 `candidate`：

1. 生成向量 `emb = embed_candidate(candidate)`；
2. 与兴趣簇计算相似度：

   ```python
   match = score_against_clusters(emb, clusters)
   sim_best   = match.sim_best
   sim_second = match.sim_second
   best_cluster_id = match.best_cluster_id
   ```

3. 结合时间与通用热度：

   ```text
   recency = f(published_at)           # 例如最近 24h/7d 内权重较高
   pop     = candidate.popularity_score

   # 边缘度：近二网络却不被一个群极端“碎压”
   border = sim_second * (1.0 - sim_best)

   interest_score = a1 * sim_best
                  + a2 * border
                  + a3 * recency
                  + a4 * pop
   ```

- `a1..a4` 为可调参数（可在 config/ENV 中设默认值）；
- v0 中 **不使用** DB 中的 `article_usage`（view_count/last_viewed_at），以避免早期稀疏数据带来噪音。

### 6.2 源特化通道（可选增量）

为少数特征丰富的源（如 HN / arxiv）提供可选的增量打分通道：

```python
def score_generic_extra(c: Candidate, base: float) -> float:
    return 0.0


def score_hn_extra(c: Candidate, base: float) -> float:
    meta = c.source_meta or {}
    points = meta.get("hn_points", 0)
    comments = meta.get("hn_comments", 0)
    return 0.5 * (points / 500.0) + 0.5 * (comments / 100.0)


SOURCE_SCORERS = {
    "hackernews": score_hn_extra,
    # 将来可以增加 "arxiv": score_arxiv_extra
}

LAMBDA_SOURCE = {
    "hackernews": 0.2,
    "default": 0.1,
}


def compute_final_score(c: Candidate, interest_score: float) -> float:
    extra = SOURCE_SCORERS.get(c.source, score_generic_extra)(c, interest_score)
    lam = LAMBDA_SOURCE.get(c.source, LAMBDA_SOURCE["default"])
    return interest_score + lam * extra
```

约束：

- 主排序分数为 `final_score`；
- `interest_score` 始终是主要部分，源特化通道只提供有限偏移；
- 源特化函数只能读取 `candidate.source_meta` 和 `candidate.popularity_score`，不得直接访问 clawsqlite 内部。

### 6.3 多样性与探索策略

生成一次 feed（如 8~12 条）时：

1. 对所有 candidates 计算：

   - `interest_score`
   - `final_score`
   - `best_cluster_id`

2. 主线（exploitation）：

   - 按 `final_score` 降序排序；
   - 按 `best_cluster_id` 分桶；
   - 轮询/配额方式选取主线条目：
     - 每个簇最多 M 条（例如 2~3 条）；
     - 总数达到主线目标（例如 8 条）后停止。

3. 探索（exploration）：

   - 从剩余候选中，基于“探索分数”选出 1~2 条，例如：
     - `score_explore` 更偏重 summary 语义（或跨簇相似度）；
     - 处于主要簇边界（sim_best 中等、sim_second 不低）；
     - 或 popularity 很高而 interest_score 中等。 
   - 这些条目用于在兴趣边界上“扫一圈”，防止视野过窄。

4. 主线 + 探索条目合并，按时间或分数略作 re-order 后交给 feed 生成模块。

---

## 7. Feed 输出与发布

### 7.1 内容生成流水线（抓全文 + 中英对译）

被选中的候选条目不直接以“链接+短摘要”形式进入 RSS，而是经过两步加工：

1. **抓取全文（fulltext scrape）**

   - 对每个入选 Candidate 调用外部抓取命令：

     ```bash
     ${CLAWFEEDRADAR_SCRAPE_CMD} <url>
     ```

   - 该命令应：
     - 从给定 URL 抓取正文；
     - 输出 markdown 或可读文本到 stdout；
     - 推荐实现方式：在 OpenClaw 环境中用一个 shell 包装器调用 `clawfetch` skill，对不同站点做细致适配。

   - 若抓取失败：
     - 可退回到原始 `summary`/`title` 做简短摘要；
     - 在 RSS 中标记为“抓取失败，仅保留简介”。

2. **中英对译摘要（LLM 处理）**

   - 将抓取到的全文（或部分截断）传给小模型（SMALL_LLM_*）：

     ```python
     def generate_bilingual_summary(fulltext: str, meta: Candidate) -> dict:
         # 返回 title_en/title_zh/summary_en/summary_zh 等字段
     ```

   - 要求输出：
     - 英文标题 + 中文标题（可选）；
     - 分段/段落级中英对译摘要，适合在 RSS 阅读器里“中英对照”阅读；
   - 若未配置 SMALL_LLM_*：
     - 可以先只输出英文原文/摘要，留出未来升级空间。

### 7.2 同时输出 XML 与 JSON

每次运行 `clawfeedradar run` 时：

- 输出一个 RSS/Atom XML 文件（例如 `radar.xml`）；
- 同时输出一个 JSON 文件（例如 `radar.json`），包含：
  - 所有选中条目的字段；
  - 打分明细（interest_score/final_score/best_cluster 等）；
  - 抓取到的 fulltext 概要（可选）以及中英摘要结果；

文件路径约定：

- XML：`${CLAWFEEDRADAR_OUTPUT_DIR}/radar.xml`
- JSON：`${CLAWFEEDRADAR_OUTPUT_DIR}/radar.json`

### 7.2 GitHub Pages 发布（可选）

若用户希望通过 GitHub Pages 对外提供 RSS：

- 环境变量：

  ```env
  CLAWFEEDRADAR_GITHUB_REPO=ernestyu/clawfeedradar-feed
  CLAWFEEDRADAR_GITHUB_BRANCH=gh-pages
  CLAWFEEDRADAR_GITHUB_FEED_PATH=feeds/radar.xml
  ```

- 逻辑建议（后续实现）：
  - clawfeedradar 在本地生成 `radar.xml` / `radar.json`；
  - 通过 `git` 或 GitHub API 将 `radar.xml` 同步到指定 repo 的指定分支和路径；
  - 用户在 RSS 阅读器订阅：

    ```
    https://<user>.github.io/<repo>/feeds/radar.xml
    ```

此功能为可选增强：v0 实现可先只在本地写文件，GitHub Pages 发布由用户自行配置；后续如有需要再集成自动 push/更新逻辑。

---

## 8. CLI 规格

### 8.1 主命令：`clawfeedradar run`

```bash
clawfeedradar run \
  --root /path/to/knowledge_data \
  --sources-file /path/to/sources.txt \
  --output /path/to/feeds/radar.xml \
  --score-threshold 0.9 \
  --max-items 12 \
  [--json]
```

参数语义：

- `--root`：clawsqlite 知识库根目录；若省略，则从 `CLAWSQLITE_ROOT` 获取。
- `--sources-file`：源列表文件路径；若省略，则从 `CLAWFEEDRADAR_SOURCES_FILE` 获取。
- `--output`：RSS XML 输出路径；若省略，则使用 `${CLAWFEEDRADAR_OUTPUT_DIR}/radar.xml`。
- `--score-threshold`：过滤掉 interest_score 低于阈值的候选（默认 0.0 或配置值）。
- `--max-items`：总输出条目数上限（主线 + 探索）。
- `--json`：当为 true 时，将选中条目及打分明细打印到 stdout（仍然写 XML/JSON 文件）。

内部步骤（逻辑约定）：

1. 加载 env/config；
2. （可选，由 cron 负责）事先在 clawsqlite 端定期跑：

   ```bash
   clawsqlite knowledge build-interest-clusters --root ...
   ```

3. 读取 `interest_clusters` 为 `ClusterInfo` 列表；
4. 根据 `sources-file` 调用各源适配器生成 Candidate 列表；
5. 对每个 Candidate：
   - 生成向量；
   - 计算 `interest_score` / `final_score` / `InterestMatch`；
6. 按 6.3 的多样性与探索策略选择条目；
7. 生成 RSS XML + JSON 文件；
8. 若配置了 GitHub Pages 发布，可额外同步 XML 到指定 repo/branch/path。

---

本 SPEC 作为 v0 的实现依据；后续如需调整（例如引入 usage、score-candidates 下放到 clawsqlite、增加别的源类型），应在本文件中更新对应章节后再动代码。 
