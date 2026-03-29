# clawfeedradar 设计草稿

本文件记录 clawfeedradar 的核心设计意图，重点是：

- 如何利用 `clawsqlite` 里的 Embedding / 兴趣簇来刻画「个人兴趣空间」；
- 如何在打分时兼顾「足够精准」和「不过度收窄视野」；
- 如何做到对数据源（HN / arXiv / RSS / 其它）**结构上通用**，同时为少数头部源保留适度的特化空间。

> 状态：设计 v0，随着实现推进可迭代。

---

## 1. 兴趣空间：summary 向量 vs tag 向量

在 clawsqlite 里，我们为每篇文章存了两类向量（基于同一个 Embedding 模型）：

- `articles_vec`：摘要向量（summary embedding）
- `articles_tag_vec`：标签向量（tags embedding）

它们各自的“性格”不同：

- **summary 向量**：
  - 覆盖文章整体语义，包括上下文、案例、写作风格；
  - 更适合刻画「这篇文章在讲什么故事」。
- **tag 向量**：
  - 聚焦于抽象出来的关键词/主题词，是对内容的一次“压缩”；
  - 更接近“我当初为什么把这篇文章收入知识库的那根兴趣轴”。

在设计 clawfeedradar 的兴趣空间时，我们的目标是：

> 在向量空间里刻画出一个「足够精确能看出用户真正兴趣，但又不会把视野锁得太窄」的个人兴趣分布。

为此，约定如下：

- **兴趣簇聚类（build-interest-clusters）**：
  - 优先使用 **tag 向量** 作为主题/兴趣的主轴；
  - 在后续版本中，摘要向量会以较小权重参与，形成一个混合向量：

    ```text
    v_interest = normalize( w_tag * normalize(v_tag)
                          + w_sum * normalize(v_summary) )
    ```

    其中：

    - `w_tag` 略大（推荐 0.7~0.8），强调标签这条“兴趣线”；
    - `w_sum` 较小（推荐 0.2~0.3），用摘要向量给簇增加厚度，避免簇过于尖锐和碎片化。

- **雷达打分（clawfeedradar 侧）**：
  - 在候选文章 vs 兴趣簇的距离计算中，同样以 tag 向量为主、summary 向量为辅：

    ```text
    sim_tag = 相对于兴趣簇中心（用 tag_vec）计算的相似度
    sim_sum = 相对于兴趣簇中心（用 summary_vec 或 v_interest）计算的相似度

    score_main    = α * sim_tag + (1 - α) * sim_sum      # 主通道：偏 tag
    score_explore = (1 - α) * sim_tag + α * sim_sum      # 探索通道：偏 summary
    ```

    - `score_main` 用于主排序，保证重点仍落在你真正关心的主题上；
    - `score_explore` 用于挑选「处于簇边界、具有探索价值」的候选，避免视野只在少数标签周围打转。

> **设计意图：** Tag 向量决定“你走哪条路”，摘要向量决定“这条路周围有哪些风景”。两者按权重混合，既有锋利的主题分辨力，又保留一定的语义宽度。

---

## 2. 打分架构：通用主通道 + 源特化通道

### 2.1 通用 Candidate 结构

所有数据源适配器（HN / arXiv / RSS / 其它）最终都要输出统一的候选结构：

```jsonc
{
  "id": "hn-123456",                 // 源内唯一 ID（hn-123456 / arxiv-xyz / rss-abc）
  "url": "https://example.com/post",
  "title": "Efficient vector search with SQLite",
  "summary": "短摘要或抓取到的正文片段",
  "tags": "hn,sqlite,vector,search",
  "source": "hackernews",            // hackernews | arxiv | rss | ...
  "published_at": "2026-03-28T07:00:00Z",

  // 各数据源自行归一化后的“热度/重要性”得分，统一映射到 0..1
  "popularity_score": 0.82,

  // 源特定的原始字段，仅供源专属通道使用；主打分逻辑不会依赖这些字段
  "source_meta": {
    "hn_points": 350,
    "hn_comments": 80,
    "rss_feed": "https://example.com/feed.xml"
  }
}
```

> **约束：** clawfeedradar 的主打分逻辑不直接依赖 `source_meta` 里的任何字段。任何源特有特征（HN points、arXiv subject 等）都必须先映射成 `popularity_score` 或通过“源特化通道”使用。

### 2.2 通用主通道：Interest Score（与源无关）

核心的兴趣打分由 `clawsqlite` 提供，入口是（未来实现）：

```bash
clawsqlite knowledge score-candidates --root ... < candidates.json > scored.json
```

clawfeedradar 只假定 `score-candidates` 会返回一个包含 `interest_score` 的结构：

```jsonc
{
  "id": "...",             // 对应 candidate.id
  "interest_score": 0.93,   // 通用兴趣分
  "top_cluster": {
    "cluster_id": 7,
    "similarity": 0.92,
    "label": "SQLite / vector search",
    "size": 38
  }
}
```

在设计上，`interest_score` 只依赖：

- 兴趣簇与候选向量之间的相似度（最近簇 / 次近簇等）；
- 簇本身的一些属性（size / 代表文章等）；
- 文章发布时间（recency）；
- 一个通用的 `popularity_score` 信号。

> 初期为了避免引入噪音，score-candidates 不使用 `article_usage`（view_count/last_viewed_at），而是完全以 Embedding + 时间 + 热度为主。usage 先用于维护/分析（例如识别从未真正读过的条目），未来视数据丰富再决定是否纳入兴趣分。

### 2.3 源特化通道：少数头部源的增量打分

对于少数特征维度特别丰富的头部源（例如：

- Hacker News：points / comments / rank / submission type；
- arXiv：subject / version / 是否 cross-list / citation 计数（如果可得）；

我们允许在主通道之外增加一条 **源特化通道**，但必须满足：

- 结构上是“增量”而不是“替代”：

  ```python
  base = interest_score  # 通用主通道
  extra = SOURCE_SCORERS.get(candidate.source, score_generic_extra)(candidate, base)
  final_score = base + LAMBDA_SOURCE.get(candidate.source, LAMBDA_SOURCE["default"]) * extra
  ```

- `SOURCE_SCORERS[...]` 只允许读取：
  - `candidate.source_meta` （源侧原始字段）；
  - `candidate.popularity_score`（已归一化后的通用热度）；
- 不得直接调用 clawsqlite 的更低层逻辑（例如额外 vec 查询），避免“源通道”和兴趣空间耦合过深。

典型实现示例：

```python
# 默认：对未知源，源特化通道不施加额外偏移

def score_generic_extra(candidate, base_score):
    return 0.0


def score_hn_extra(candidate, base_score):
    meta = candidate.get("source_meta") or {}
    points = meta.get("hn_points", 0)
    comments = meta.get("hn_comments", 0)
    # 举例：略微偏好高分+高评论的 HN 帖子
    return 0.5 * (points / 500.0) + 0.5 * (comments / 100.0)


SOURCE_SCORERS = {
    "hackernews": score_hn_extra,
    # 将来可以增加 "arxiv": score_arxiv_extra 等
}

LAMBDA_SOURCE = {
    "hackernews": 0.2,
    "default": 0.1,
}


def combined_score(candidate, interest_score):
    extra = SOURCE_SCORERS.get(candidate["source"], score_generic_extra)(candidate, interest_score)
    lam = LAMBDA_SOURCE.get(candidate["source"], LAMBDA_SOURCE["default"])
    return interest_score + lam * extra
```

> **设计意图：** 这样既能让 HN / arXiv 等头部源利用自己的丰富特征，又不把主打分逻辑“焊死”在某个源的字段上。对任何未知源，只要填好通用字段，也能获得合理的 `interest_score` 和 `final_score`。

---

## 3. 排序策略：主线 + 多样性 + 探索

即便打分设计合理，如果最终只取 `final_score` 的 top N，仍然有可能被少数几个兴趣簇垄断，从而逐步收窄视野。

为此，clawfeedradar 在生成一次周期 feed 时的排序策略大致为：

1. **主线候选（exploitation）**

   - 按 `final_score` 全局排序得到一个长列表；
   - 按兴趣簇（或 `top_cluster.cluster_id`）做分桶；
   - 逐个簇轮询/配额式选取，例如：
     - 每个簇最多选 M 条（例如 2~3 条）；
     - 总数达到主线目标（例如 8 条）后停止；

   这样：

   - 高频兴趣簇会有更多条目，但不能“一家独大”；
   - 次要兴趣簇仍有机会出现在日报中。

2. **探索候选（exploration）**

   - 从剩余候选中挑选一小批“探索项”：
     - 例如：
       - 在 `score_explore`（偏重 summary 语义）的排序中得分较高；
       - 与当前主兴趣簇距离适中（既不太远也不完全重合）；
       - 或者全局 `popularity_score` 很高但 `interest_score` 中等。
   - 从这些探索候选中再抽取 1~2 条加入当期 feed。

   这类条目可以在 UI/文案中标明为“尝试看看”或“探索推荐”，心理预期与主线不同。

3. **最终输出**

   - 主线 + 探索条目合并，按时间或 `final_score` 做轻微 re-rank（保证阅读流畅）；
   - 对每条记录生成中英摘要，渲染为 RSS item。

> **设计意图：** 主线保证“真正贴合你长期兴趣”的内容始终占大头，多样性避免单一簇垄断，探索项则专门负责在兴趣边界处“扫一眼”，防止个人视野因为推荐系统而越来越窄。

---

## 4. 数据源配置：外部列表 + 自动识别

### 4.1 数据源列表文件

为保持配置简单，clawfeedradar 使用一个独立的源列表文件（例如 `sources.txt`）：

```text
# 一行一个源，可以是 URL 或简写标识
https://news.ycombinator.com/          # HN: 由 HN adapter 识别
https://arxiv.org/list/cs.LG/recent    # arXiv: 由 arXiv adapter 识别
https://example.com/feed.xml           # 普通 RSS 源
```

雷达在运行时会：

- 逐行读取该文件；
- 根据 URL 或前缀模式自动检测源类型：
  - `news.ycombinator.com` → `source="hackernews"`；
  - `arxiv.org` → `source="arxiv"`；
  - 其它 → `source="rss"`；
- 分别交给对应的 adapter 拉取候选，然后统一转成 Candidate 结构。

> **设计意图：** 用户只需要维护一个简单的“我关心的源列表”，无需在配置里到处显式写“这是 HN / 这是 arXiv”；源类型识别由代码负责，扩展新适配器时也更自然。

### 4.2 环境变量示例（ENV example）

clawfeedradar 需要自己的 `ENV.example` 用于示例配置，主要包括：

- 与 clawsqlite 共用的 Embedding 配置（用于 score-candidates / 聚类等）：

  ```env
  # Embedding service (shared with clawsqlite)
  EMBEDDING_BASE_URL=https://embed.example.com/v1
  EMBEDDING_MODEL=your-embedding-model
  EMBEDDING_API_KEY=sk-your-embedding-key
  CLAWSQLITE_VEC_DIM=1024
  ```

- clawsqlite 根目录路径（便于雷达找到知识库 DB）：

  ```env
  # Knowledge base root used by clawsqlite
  CLAWSQLITE_ROOT=/home/node/.openclaw/workspace/knowledge_data
  ```

- 源列表文件路径：

  ```env
  # Source list for clawfeedradar (one URL or identifier per line)
  CLAWFEEDRADAR_SOURCES_FILE=/home/node/.openclaw/workspace/clawfeedradar/sources.txt
  ```

- 其它：
  - 小模型（用于摘要/翻译）可以是可选配置；
  - 输出目录（feed 文件写入位置）也可以提供 env 默认值。

后续可以在本仓库根目录添加 `ENV.example`，并在 README 中指向该文件，作为部署前的参考模板。
