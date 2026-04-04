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

---

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

CLAWSQLITE_INTEREST_TAG_WEIGHT=0.75
```

### 4.2 抓取与输出

```env
CLAWFEEDRADAR_OUTPUT_DIR=/path/to/feeds
CLAWFEEDRADAR_SCRAPE_CMD="/path/to/clawfetch_wrapper.sh"
CLAWFEEDRADAR_SCRAPE_WORKERS=4
# CLAWFEEDRADAR_HTTP_USER_AGENT=...

CLAWFEEDRADAR_MAX_ITEMS=12
```

抓取策略：

- 使用 `ThreadPoolExecutor(max_workers=CLAWFEEDRADAR_SCRAPE_WORKERS)`；
- 以 host 为 key 做锁：同 host 内串行、跨 host 并行；
- `fetch_fulltext` 内部负责重试和退避。

### 4.3 打分参数

```env
CLAWFEEDRADAR_W_RECENCY=0.05
CLAWFEEDRADAR_W_POPULARITY=0.05
CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS=3

CLAWFEEDRADAR_INTEREST_SIGMOID_K=4.0

CLAWFEEDRADAR_LAMBDA_HN=0.2
CLAWFEEDRADAR_LAMBDA_ARXIV=0.1
```

### 4.4 LLM 相关

```env
SMALL_LLM_BASE_URL=...
SMALL_LLM_MODEL=...
SMALL_LLM_API_KEY=...

CLAWFEEDRADAR_LLM_CONTEXT_TOKENS=8096
CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS=2400
CLAWFEEDRADAR_LLM_TAG_MAX_PER_ITEM=12
CLAWFEEDRADAR_LLM_SLEEP_BETWEEN_MS=500

CLAWFEEDRADAR_LLM_SOURCE_LANG=auto
CLAWFEEDRADAR_LLM_TARGET_LANG=zh
```

---

## 5. 源适配器

统一入口：

```python
def fetch_candidates_from_source(source_type: str, source_url: str, max_items: int | None = None) -> list[Candidate]:
    ...
```

（略）

---

## 6. 打分与排序

（略，参见英文版 SPEC_en.md 中同名章节）

---

## 7. 选择与输出

（略，参见英文版 SPEC_en.md 中同名章节）

---

## 8. 通过 Git 发布 feeds（GitHub Pages / Gitee Pages）

clawfeedradar 支持在 pipeline 结束后，将生成的 XML/JSON 自动推送到一个 git 仓库，
从而配合 GitHub Pages / Gitee Pages 这样的免费静态托管，把 RSS feed 公开出去。

### 8.1 环境变量配置

在 `.env` 中配置以下变量：

```env
# 可以是 GitHub，也可以是 Gitee，只要 git 能访问即可
CLAWFEEDRADAR_PUBLISH_GIT_REPO=git@github.com:yourname/clawfeedradar-feed.git
CLAWFEEDRADAR_PUBLISH_GIT_BRANCH=gh-pages
CLAWFEEDRADAR_PUBLISH_GIT_PATH=feeds
```

或 Gitee：

```env
CLAWFEEDRADAR_PUBLISH_GIT_REPO=git@gitee.com:yourname/clawfeedradar-feed.git
CLAWFEEDRADAR_PUBLISH_GIT_BRANCH=gh-pages
CLAWFEEDRADAR_PUBLISH_GIT_PATH=feeds
```

使用前置条件：

- 远端仓库已经创建，并且可以通过 git clone/push 访问；
- 在 GitHub/Gitee 的仓库设置里，打开 Pages 功能：
  - 例如选择 `gh-pages` 分支 + `feeds/` 目录；
- 运行环境中安装了 git，并且已经配置好认证：
  - 对 SSH URL（git@github.com/...）要配置好 SSH key；
  - 对 HTTPS URL，要有凭证助手或 PAT。

### 8.2 行为说明

当上述变量配置好之后，每次 `run` / `schedule` 成功写出 XML/JSON 后，clawfeedradar 会：

1. 在当前工作目录下维护一个 `./.publish/<slug>/` 本地 clone：
   - 例如 `git@gitee.com:user/feed.git` → `./.publish/user-feed/`；
2. 在该 clone 里 checkout 到指定分支（不存在则尝试新建）；
3. 把本次生成的 `*.xml` / `*.json` 拷贝到 `<clone>/<PATH>/` 目录下；
4. 依次执行 `git add`、`git commit`（若无变更则忽略错误）、`git push origin <branch>`。

未配置 `CLAWFEEDRADAR_PUBLISH_GIT_REPO` 时，此步骤会被跳过，对主流程没有影响。

如果 publish 过程中出错（clone / checkout / push）：

- 会在 stdout 打出一条以 `[error]` 开头的提示，并附上下一步建议；
- 在 `clawfeedradar` logger 中记录 error；
- 对 publish 步骤返回非零退出码，但 **不会让打分 pipeline 崩溃**：
  - 本地 XML/JSON 始终会写成功；
  - 只是远端仓库没有被更新。

这样配置好之后，用户只需在 RSS 阅读器里订阅 GitHub Pages / Gitee Pages
对应的 URL，例如：

```text
https://yourname.github.io/clawfeedradar-feed/feeds/bbc-tech.xml
# 或
https://yourname.gitee.io/clawfeedradar-feed/feeds/bbc-tech.xml
```

即可在任何支持 RSS 的客户端中，稳定访问由 clawfeedradar 生成的个人「读报雷达」。
