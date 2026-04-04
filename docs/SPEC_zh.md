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

后续内容与英文版 SPEC_en.md 对应，描述相同的 v1 行为（打分、LLM、CLI 等），此处不再重复。
