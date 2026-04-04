# 把「读报」变成一门可调的工程：clawfeedradar 背后的兴趣雷达

> 这篇文章是对开源项目 **clawfeedradar** 的一次完整介绍：
> 
> - 它解决的究竟是什么问题？
> - 它是怎么把你自己的知识库，变成一个「读报雷达」的？
> - 打分和筛选的公式长什么样，能不能解释、能不能调？
> - 在 OpenClaw 环境里，实际该怎么用？

## 一、为什么要做「读报雷达」？

如果你是一个技术人，大概会有这样的日常：

- 每天刷 HN / Reddit / RSS / Twitter / 公众号，动辄几百条信息；
- 有些内容你看完觉得「非常对胃口」，但再回头想抓类似的，却抓不住规律；
- 推荐算法永远是平台的，不是你的：你无法控制它的逻辑，也很难审计它为什么给你推这条。

更现实一点说：

> 真正有价值的内容越来越多，但「我的精力」没有跟着线性增长。

我更想要的是：

- 不是「所有人都在看什么」，而是「和我过去认真读过、收藏过的东西类似的内容」；
- 这个判断过程要足够简单、可解释、可调参；
- 最好能跑在自己的环境里，而不是交给一个黑盒推荐系统。

**clawfeedradar** 就是朝着这个方向做的一件小工具：

> 基于你自己的 `clawsqlite` 知识库和兴趣簇，从外部信息源里筛选出「你可能感兴趣」的文章，自动生成一个可以订阅的个人 RSS feed。

它不负责「懂世界」，只负责一件事：

> 在你已经写好的「个人知识库」周围，画出一个兴趣空间，然后每天围着这个空间打一圈雷达，把落在边上的那些新闻/文章收集起来。


## 二、系统全貌：两块砖拼出的雷达

整个系统，拆开看只有两块核心组件：

1. **`clawsqlite-knowledge`**：维护个人知识库和兴趣簇；
2. **`clawfeedradar`**：围绕兴趣簇构建「读报雷达」。

可以把它想象成这样一张图：

```text
你的文章 / 笔记 / 剪藏
  ↓（ingest + embedding + 聚类）
clawsqlite-knowledge
  ↓（interest_clusters: N 维兴趣空间）
clawfeedradar
  ↑             ↓
HN / RSS / ...  →  interest_score + final_score → RSS + JSON
```

- 上半截：你先用 `clawsqlite-knowledge` 把自己的知识库建好，做 Embedding，跑兴趣簇聚类；
- 下半截：clawfeedradar 只引用这些兴趣簇，以它们为「坐标系」，对外面的文章做「距离测量」。

### 在 OpenClaw 里如何准备环境？

如果你是在 OpenClaw 里用这套东西，推荐的顺序是：

1. 安装 `clawsqlite-knowledge` 这个 skill：

   ```bash
   openclaw skills add clawsqlite-knowledge
   ```

   或者直接从网页目录进入：

   - <https://clawhub.ai/skills/clawsqlite-knowledge>

2. 按照 skill 的 README 导入你自己的内容（org/Markdown/网页等），并运行：

   ```bash
   clawsqlite knowledge build-interest-clusters --root /path/to/your/clawsqlite/data
   ```

   这一步会创建 `interest_clusters` 等表，把你的历史阅读/笔记压缩成若干个兴趣簇。

3. 在同一个工作区克隆/安装 `clawfeedradar`，配置 `.env` 指向同一个 `CLAWSQLITE_ROOT` / `CLAWSQLITE_DB`，之后雷达就可以复用这套兴趣空间了。


## 三、兴趣空间：先把「你喜欢什么」明确下来

很多推荐系统会把「兴趣」当成隐变量：用户点得多的，就是它以为的「你喜欢」。

clawfeedradar 刻意反过来：

- 不猜你的兴趣，而是直接用你过去认真读过、整理过的文章来构建兴趣簇；
- 这些簇都保存在一个普通的 SQLite 里，你可以查、可以可视化、可以审计。

### 3.1 两种向量：摘要 vs 标签

在 `clawsqlite-knowledge` 里，每篇文章会被编码成两类向量：

- **摘要向量** `articles_vec`：
  - 输入是文章的摘要文本；
  - 更偏「文章在讲什么故事」。
- **标签向量** `articles_tag_vec`：
  - 输入是你给文章写的 TAG/keywords；
  - 更偏「我为什么把它收入知识库」。

这两个向量反映的是不同层面的「兴趣」：

- 摘要向量给的是内容空间里的位置；
- 标签向量更像是你脑子里的「兴趣坐标轴」。

### 3.2 构建兴趣簇：一个可调的混合

`clawsqlite knowledge build-interest-clusters` 在做的事情，大致是：

1. 从 DB 里选出所有有摘要/标签的文章；
2. 用一个简单的线性混合，把摘要向量和标签向量叠在一起：

   ```text
   v_interest = (1 - w_tag) * v_summary + w_tag * v_tag
   ```

   - 混合权重由 `CLAWSQLITE_INTEREST_TAG_WEIGHT` 控制，默认是 **0.75**；
   - 也就是：75% 看 tag，25% 看 summary。

3. 在这些 `v_interest` 上跑 k-means 聚类，再对小簇做合并；
4. 最终得到一堆 `interest_clusters`：每个簇有 `id / label / size / centroid`。

这一步的设计目标是：

> 让你的兴趣空间，既有「主题分辨力」，又不会被某几个偶然的向量点带偏。


## 四、雷达：从 HN / RSS 里筛出「你可能会点开的那几条」

有了兴趣簇，接下来才轮到 clawfeedradar 上场。

它的工作可以分成三步：

1. 抓候选；
2. 把候选映射到兴趣空间；
3. 在兴趣空间里做打分和筛选。

### 4.1 抓取候选

目前内建的源适配器是基于 RSS 的：

- HN RSS（例如 `https://hnrss.org/frontpage`）；
- 普通 RSS / Atom（BBC / 各类博客 / 新闻源）。

所有源最终会被转成统一的 `Candidate` 结构：

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
    popularity_score: float  # 0..1
    source_meta: dict[str, Any]
```

其中：

- `popularity_score`：
  - HN 源会从 `points/comments` 里解析出一个 0..1 的热度；
  - 普通 RSS 源默认 **0.0**（不伪造一个「中性 0.5」）。
- `source_meta`：
  - 例如 HN 的原始 `hn_points` / `hn_comments`；
  - 主打分逻辑不直接用，只给源特化通道参考。

抓取过程中还有两件小事：

- 最近 7 天 `seen_urls` 去重，避免重复推同一条；
- 以 host 为单位做锁：
  - 同一个 host 内串行；
  - 不同 host 之间可以并发抓取。

### 4.2 构造兴趣向量：长摘要 + TAG + embedding

对每个候选：

1. **抓全文**：通过你配置的 `CLAWFEEDRADAR_SCRAPE_CMD`（通常是 `clawfetch` 的 wrapper）获取正文；
2. **构造长摘要** `_build_long_summary(fulltext)`：
   - 按空行切段；
   - 从头累加段落，直到接近 ~1200 字符，在段落边界截断；
   - 如果最后一段没包含，则再追加末段；
   - 如果全文抓取失败，就退回 `title + "\n\n" + summary`。
3. **批量生成 TAG**：
   - 使用小 LLM，对一批长摘要调用一次 `generate_tags_bulk`；
   - 支持部分成功 + 重试，失败的条目会被置空 TAG。
4. **embedding**：
   - 所有长摘要通过 `embed_texts` 顺序调用 embedding 服务；
   - TAG 文本逐条调用 `embed_text`，失败退化为全零向量；
   - 所有错误都降级为零向量 + WARNING，不会炸掉整个 pipeline。
5. **混合成兴趣向量**：
   - 和 clawsqlite 一样，用 `CLAWSQLITE_INTEREST_TAG_WEIGHT` 混合 summary 和 tag embedding；
   - 默认仍然是「标签 75%、摘要 25%」。

这样，每篇候选文章就变成了兴趣空间里的一个点。


## 五、打分：一条干净的主公式 + 少量偏置

clawfeedradar 的打分刻意做得很「单线程」：

- **主公式只有一条**，没有隐藏的 if/else 分支；
- 时间和热度只做轻微偏置，不盖过兴趣本身；
- 源特化通道只是个小补丁，不会把整体逻辑拐到别处去。

### 5.1 线性兴趣分：簇加权相似度

先看主干的线性部分。

1. 从 clawsqlite 读取所有兴趣簇：`ClusterInfo(id, label, size, centroid)`；
2. 预先计算簇权重：

   ```text
   total_size = Σ_k max(1, size_k)
   cluster_weight_k = size_k / total_size
   ```

   - 大簇自然权重更高；
   - `size_k` 过小的簇，在构建阶段就已经被合并掉了。

3. 对每篇候选的兴趣向量 `emb`，计算和所有簇的余弦相似度：

   ```python
   interest_raw = 0.0
   best_cluster_id = -1
   best_sim = 0.0
   second_sim = 0.0

   for cluster in clusters:
       sim = cosine(emb, cluster.centroid)
       sim = max(sim, 0.0)  # 负相似度直接截掉
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

- `interest_raw`：线性兴趣分，范围在 0~1；
- `best_cluster_id` / `best_cluster_weight`：告诉你这条落在大簇还是小簇上；
- `sim_best` / `sim_second`：解释性用，例如看它是不是「在两个簇的边缘」。

### 5.2 S 型 sigmoid：把分数往两边推开

光有 `interest_raw`，很多条会挤在 0.4~0.7 一坨，阈值不好设。于是我们在上面套了一层 S 型 sigmoid：

```python
# K 来自 CLAWFEEDRADAR_INTEREST_SIGMOID_K（默认 4.0，你可以调成 8/10）
z = K * (interest_raw - 0.5)
interest = 1.0 / (1.0 + exp(-z))
```

这一步：

- 保持排序不变（sigmoid 是严格单调递增）；
- 把中段拉开，让「略有兴趣」和「明显有兴趣」在数值上拉出差距；
- 把非常低的分压得更低，把非常高的分抬得更接近 1。

在 JSON 里，你会同时看到：

- `interest_score_raw`：原始线性分；
- `interest_score`：sigmoid 之后的分，推荐用它来做阈值筛选。

### 5.3 时间 & 热度：轻微偏置

然后，在 `interest` 上再叠加一点时间和热度。

- 时间 weight：

  ```python
  rec = recency_weight(published_at, now, half_life_seconds)
  # half_life_seconds = CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS * 24 * 3600
  ```

  - 半衰期默认为 3 天；
  - 最近 1 天内的文章得分更高，几天前的会逐渐衰减。

- 热度：

  ```python
  pop = clamp(candidate.popularity_score, 0.0, 1.0)
  ```

  - HN 用 points/comments 归一；
  - RSS 默认是 0。

- 偏置项：

  ```python
  interest_bias = w_recency * rec + w_popularity * pop
  biased_interest = interest + interest_bias
  ```

  默认：`w_recency = w_popularity = 0.05`，只是轻轻拉一把。你也可以 per-run 用 `--w-recency` / `--w-popularity` 临时覆盖。

### 5.4 源特化通道 & final_score

最后，有一条非常薄的源特化通道，例如 HN：

- 从 `source_meta` 里面拿出 points/comments；
- 算出一个 0..1 的附加分 `extra`；
- 乘上一个 `λ_HN` 之后加回 `biased_interest`。

最终的 `final_score` 就是：

```text
final_score = biased_interest + λ_source * extra
```

排序用的是 `final_score`，但 JSON 里会同时保留 `interest_score`、`interest_score_raw` 和 `final_score`，方便你用不同视角看数据。


## 六、输出与调试：RSS+JSON，全部摊在桌面上

每次跑 `clawfeedradar run` 或 `schedule`，都会为每个源写一对文件：

- `{label}.xml`：RSS feed，给任何标准 RSS 阅读器订阅；
- `{label}.json`：sidecar JSON，给你自己或 Agent 做调试。

JSON 里除了基本字段，还包含：

- 打分明细：
  - `interest_score` / `interest_score_raw` / `final_score`；
  - `best_cluster_id` / `best_cluster_weight`；
- LLM 结果：
  - `summary_preview`（预览摘要，适合在列表里快速扫一眼）；
  - `body_bilingual`（中英对照正文）。

调试策略很简单：

1. 先不在意 RSS，专心看 JSON；
2. 用简单的脚本看 `interest_score` 分布直方图；
3. 根据分布调 `CLAWFEEDRADAR_INTEREST_SIGMOID_K`、阈值、`w_recency/w_popularity`；
4. 稳定之后再接入 RSS 阅读器日常使用。


## 七、在 OpenClaw 里的最佳实践

最后，总结一下在 OpenClaw 里跑这套东西的推荐路径：

1. 安装并配置 `clawsqlite-knowledge`，建好兴趣簇；
2. 安装/克隆 `clawfeedradar`，参考 `ENV.example` 写 `.env`；
3. 用 `clawfeedradar run` 对单个源做调试，观察 JSON 的分布；
4. 写好 `sources.json`，用 `clawfeedradar schedule` 定时跑多源；
5. 日常只需要在 RSS 阅读器里订阅生成好的 XML，就可以每天享受一份「和自己知识库对齐」的读报列表。

从这个角度看，clawfeedradar 做的事情其实很朴素：

> 不去猜你是谁，而是认真复用你已经写下来的东西。

它把这些「你对世界的标注」，转成一个可计算的兴趣空间，再拿这个空间去过滤每天汹涌而来的信息流。

如果你已经在用 OpenClaw 和 clawsqlite 记录和整理自己的知识，那么给自己加一个这样的「读报雷达」，其实就是多写了一个 cron 而已。