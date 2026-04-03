# clawfeedradar

个人「读报雷达」：围绕 `clawsqlite` 知识库，自动从多个信息源（Hacker News、RSS、arXiv 等）中挑选**你可能感兴趣**的文章，生成一个专属 RSS feed（支持中英对照摘要），让你在常用 RSS 阅读器里看到真正贴合自己兴趣的技术流和论文流。

> 项目还在设计阶段，这个 README 先作为愿景 + 高层设计草稿，后续实现过程中会细化。

---

## 1. Overall 思路

把系统拆成两层：

- **核心引擎：clawsqlite**
  - 负责存你的知识库（articles + 摘要向量 + 标签向量）。
  - 新增「兴趣簇 + 候选打分」这两类能力：
    - 定期对整个库做聚类，得到一组代表你长期兴趣方向的 **interest clusters**；
    - 给一批「外部候选文章」（HN / RSS / arXiv）打“兴趣分”，看它们落在哪些簇里。

- **外部应用：clawfeedradar（本仓库）**
  - 负责和各种信息源打交道：抓 Hacker News / RSS / arXiv；
  - 把这些候选文章喂给 `clawsqlite` 做兴趣打分；
  - 对分数够高的文章做中/英摘要，生成一个可以被 RSS 阅读器订阅的 `*.xml` feed；
  - 将 feed 推送到一个稳定的 URL（GitHub Pages 或你的自托管站点）。

一句话：**clawsqlite 负责“知道你喜欢什么”，clawfeedradar 负责“去外面帮你找类似的东西，并打包成可阅读的 feed”。**

---

## 2. 角色分工

### 2.1 clawsqlite 侧（不在本仓库实现）

在 `clawsqlite` 仓库中，我们计划新增：

1. 两个 `knowledge` 子命令：
   - `clawsqlite knowledge build-interest-clusters`：
     - 定期（例如每天）对知识库里的文章做聚类；
     - 构建出代表你长期兴趣的若干 **兴趣簇**；
     - 将簇中心与成员关系持久化到 DB 里（新增两张可选表）。

   - `clawsqlite knowledge score-candidates`：
     - 接收一批来自外部的信息源的候选文章（JSON 输入）；
     - 对每条候选，基于兴趣簇做打分：
       - 落在哪个簇？
       - 与该簇的相似度是多少？
     - 返回一个带 `interest_score` 和簇信息的 JSON 输出，供上层应用决定是否推荐。

2. 新增两张可选表（仅在显式调用 `build-interest-clusters` 时创建）：

   - `interest_clusters`
     - 存储每个簇的中心向量、规模和自动生成的标签（label）。
   - `interest_cluster_members`（可选）
     - 记录簇内包含哪些文章、成员权重等，用于调试和可视化。

> 这些 schema 变更都是「向后兼容 + 按需启用」的：
> - 不调用新的命令，就不会创建新表；
> - 不影响传统的 `ingest` / `search` / `reindex` 等行为。

### 2.2 clawfeedradar 侧（本仓库实现）

本项目不关心具体的 DB 细节，只把 `clawsqlite` 当成一个“兴趣打分服务”。

职责包括：

1. **数据源适配（Source Adapters）**
   - `Hacker News`：
     - 通过官方 API 拉取 `topstories` / `newstories`；
   - `RSS`：
     - 从任意外部 RSS feed 拉取新条目；
   - `arXiv` / 其它：
     - 后续可以按需扩展。

   所有数据源最终汇总为统一的「候选文章 JSON 格式」，与具体源解耦，形如：

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

2. **调度 & 打分（Scheduler + Scoring）**

   典型的周期性流程：

   - 每天一次（或更频繁）：

     ```bash
     clawsqlite knowledge build-interest-clusters --root /path/to/knowledge_data
     ```

     让 `clawsqlite` 重新用整个知识库构建兴趣簇，反映你最近的阅读/收藏变化。

   - 每 8 小时一次：

     ```bash
     clawfeedradar run \
       --root /path/to/knowledge_data \
       --sources hn,arxiv,rss \
       --score-threshold 0.9 \
       --output ./feeds/radar.xml
     ```

     内部步骤：

     1. 从各个源（HN / RSS / arXiv）拉候选文章，统一转成上面的 Candidate 结构；
     2. 对每条候选，调用 `clawsqlite knowledge score-candidates` 获取 **通用兴趣分**（interest_score），该分数只依赖：
        - 文章向量与兴趣簇的相似度（最近簇 / 次近簇等）；
        - 簇内文章的 usage（view_count / last_viewed_at）；
        - 文章发布时间（recency）；
        - `popularity_score`（已归一化的通用热度）。
     3. 针对少数头部源（例如 `hackernews`、`arxiv`），通过可选的 **源特化通道** 做一点增量打分：

        ```python
        base = interest_score  # 通用主通道
        extra = SOURCE_SCORERS.get(candidate.source, score_generic_extra)(candidate, base)
        final_score = base + LAMBDA_SOURCE.get(candidate.source, LAMBDA_SOURCE["default"]) * extra
        ```

        - `score_hn_extra` 等函数只看 `candidate.source_meta` / `popularity_score` 这类源特定字段；
        - 主通道逻辑不依赖任何 HN/arXiv 特有字段，保持对所有源通用。

     4. 在排序时加入 **多样性与探索** 策略：
        - 主列表按 `final_score` 排序，但每个兴趣簇/主题最多取若干条，避免被单一簇垄断；
        - 额外从“处于簇边界”或“全局高热度但相似度一般”的候选中抽取 1–2 条作为探索项，保证视野不过度收窄。

     5. 对选中的条目，用小型 LLM（或你配置的翻译服务）生成中英摘要，并渲染成一个 RSS feed (`*.xml`) 文件。

3. **输出：生成专属 RSS feed**

   - `clawfeedradar` 生成的 RSS 文件可以：
     - 推送到 GitHub Pages 对应的仓库（例如 `ernestyu/clawfeedradar-feed`）；
     - 或者同步到你的自托管站点（nginx / Python http.server 等）。
   - 你的 RSS 阅读器只需要订阅一个 URL（例如：

     ```
     https://<your-user>.github.io/clawfeedradar-feed/hn-radar-<token>.xml
     ```

     ）就能看到：

     - 标题（英文 + 中文简译）；
     - 中英对照摘要；
     - 原文链接；
     - 可选的「这篇文章最接近你知识库里的哪些文章/兴趣簇」等解释信息。

---

## 3. 典型工作流示意

假设你已经有一套 `clawsqlite-knowledge` 知识库，里面存着你这些年手工收集的文章/论文：

1. **每天凌晨**：

   ```bash
   clawsqlite knowledge build-interest-clusters --root /home/node/.openclaw/workspace/knowledge_data
   ```

   - 从 DB 把文章向量全部拉出来；
   - 聚类形成若干兴趣簇（如「LLM/Agents」「SQLite/向量检索」「量化/风控」等）；
   - 将簇中心和成员写入 `interest_clusters` 表。

2. **每 8 小时一次**：

   ```bash
   clawfeedradar run \
     --root /home/node/.openclaw/workspace/knowledge_data \
     --sources hn \
     --score-threshold 0.9 \
     --output ./feeds/hn-radar.xml
   ```

   clawfeedradar 会：

   - 调 HN API 拿最新一批 stories；
   - 对每条 story 抓正文/生成摘要，整理成 candidates；
   - 把 candidates 喂给 `clawsqlite knowledge score-candidates`，拿到兴趣分和对应簇；
   - 只保留“分数明显高”的文章；
   - 用 LLM 生成中英摘要；
   - 写出 `hn-radar.xml` RSS 文件，并（通过 git 或其它方式）同步到公开可访问的 URL。

3. **你这边的体验**：

   - 打开你熟悉的 RSS 阅读器，订阅一次该 URL；
   - 每天看到的是一条「已经按你个人兴趣过滤和排序过」的 HN/论文/博客流；
   - 觉得某篇值得永久收藏，就再用 `clawsqlite-knowledge` / `clawsqlite knowledge ingest` 把它写回知识库，反过来继续丰富兴趣簇。

---

## 4. 状态说明

当前仓库处于 **设计起步阶段**：

- ✅ 仓库创建 & 命名：`clawfeedradar`
- ✅ 确认与 `clawsqlite` 的边界：
  - clawsqlite 提供聚类 + 候选打分的 CLI；
  - clawfeedradar 做数据源适配、调度、feed 生成。
- 🔜 下一步计划：
  1. 明确 `score-candidates` CLI 的 JSON schema（输入/输出字段）；
  2. 设计 `clawfeedradar` 的命令行接口（如 `run` 子命令、配置文件结构）；
  3. 选定最小可用数据源（优先 Hacker News），实现 HN→RSS 的 MVP。

后续随着实现推进，这个 README 会拆成：

- 高层说明（本文件保留简洁版）；
- `docs/design.md`：详细设计文档；
- `docs/sources.md`：各数据源适配器说明；
- `docs/deployment.md`：如何在本地/服务器上部署 & 配置 RSS 输出。

### CLI 概览（v0 实现）

当前实现的两个子命令：

- `clawfeedradar run`：针对单个源 URL（例如 HN frontpage）拉取候选、打分并生成一对 XML+JSON：

```bash
clawfeedradar run \n  --root /path/to/knowledge_data \n  --url https://hnrss.org/frontpage \n  --output ./feeds/hn-frontpage.xml \n  --score-threshold 0.4 \n  --max-items 5
```

- `clawfeedradar schedule`：读取 `sources.json`（由你维护的源配置文件），按各源的 `interval_hours` 定期跑 radar，输出每源各自的 XML/JSON。

`max-items` 遵循 `CLI > 环境变量 (CLAWFEEDRADAR_MAX_ITEMS) > 默认 12` 的优先级。


## 使用指南（v0）

### 1. 环境变量配置

复制 `ENV.example` 为 `.env`，按你的机器修改：

- clawsqlite 知识库：
  - `CLAWSQLITE_ROOT` 指向 `knowledge_data` 根目录
  - `CLAWSQLITE_DB` 如需要可显式覆盖
- Embedding 服务（和 clawsqlite 共用）：
  - `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_API_KEY`
  - `CLAWSQLITE_VEC_DIM` 必须和 embedding 模型维度一致
- clawfeedradar 输出：
  - `CLAWFEEDRADAR_OUTPUT_DIR`：XML/JSON 输出目录
- 小 LLM（摘要 + 中英对照）：
  - `SMALL_LLM_BASE_URL` / `SMALL_LLM_MODEL` / `SMALL_LLM_API_KEY`
  - `CLAWFEEDRADAR_LLM_MAX_OUTPUT_CHARS`：单次调用输出预算（字符级）
  - `CLAWFEEDRADAR_LLM_SLEEP_BETWEEN_MS`：两次调用间隔（毫秒）
  - `CLAWFEEDRADAR_LLM_SOURCE_LANG` / `CLAWFEEDRADAR_LLM_TARGET_LANG`
  - `CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS`：每个中英对照段的最大字符数（一屏），例如 2400
- 抓全文：
  - `CLAWFEEDRADAR_SCRAPE_CMD` 指向一个调用 clawfetch 的 wrapper
  - `CLAWFEEDRADAR_SCRAPE_WORKERS` 控制抓取并发（默认 4）
- 评分权重：
  - `CLAWFEEDRADAR_W_SIM_BEST` / `W_SIM_SECOND` / `W_RECENCY` / `W_POPULARITY`
  - `CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS`：recency 半衰期（天）
- 默认条目数：
  - `CLAWFEEDRADAR_MAX_ITEMS`：不写 `--max-items` 时的默认条数

### 2. 单源调试：`clawfeedradar run`

示例：只对 HN frontpage 跑一轮 radar：

```bash
clawfeedradar run \n  --root /home/node/.openclaw/workspace/knowledge_data \
  --url https://hnrss.org/frontpage \
  --output ./feeds/hn-frontpage.xml \
  --score-threshold 0.4 \
  --max-items 5 \
  --source-lang en \
  --target-lang zh
```

行为：

- 从给定 URL 拉取候选（当前实现支持 HN RSS / 普通 RSS）
- 对每条候选做 embedding、和兴趣簇计算 `sim_best` / `sim_second` 等
- 按公式计算兴趣分：
  - 主簇：`W_SIM_BEST * sim_best`
  - 边缘补偿：`W_SIM_SECOND * (sim_second * (1 - sim_best))`
  - 时间衰减：`W_RECENCY * recency`（按 `RECENCY_HALF_LIFE_DAYS` 计算）
  - 热度：`W_POPULARITY * popularity_score`
- 过滤掉 `interest_score < score_threshold` 的候选
- 按 `final_score` 排序，取前 `max-items` 条
- 对每条选中条目：
  - 只抓一次全文（带重试 + backoff），构造长摘要+中文预览
  - 用小 LLM 生成中英对照正文，按屏段切分（由 `LLM_MAX_PARAGRAPH_CHARS` 控制）
- 输出：
  - `./feeds/hn-frontpage.xml`：RSS feed，`<description>` 包含 preview + bilingual body
  - `./feeds/hn-frontpage.json`：JSON sidecar，包含 fulltext / summary_preview / body_bilingual / 打分信息

### 3. 多源调度：`clawfeedradar schedule`

`sources.json` 示例：

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

命令：

```bash
clawfeedradar schedule \n  --root /home/node/.openclaw/workspace/knowledge_data \
  --sources-json /home/node/.openclaw/workspace/clawfeedradar/sources.json \
  --output-dir /home/node/.openclaw/workspace/clawfeedradar/feeds
```

行为：

- 遍历 `sources.json` 的每个 entry：
  - 根据 `interval_hours` + `last_success_at` 判定是否到点
  - 若到点：调用与 `run` 相同的 pipeline
  - 每个源输出 `{label}.xml` + `{label}.json` 到 `output-dir`
  - 更新该 entry 的 `last_success_at` / `last_error`

### 4. English quick summary

- Configure env via `.env` (see `ENV.example`) for: knowledge root, embedding, LLM, scraping, scoring weights.
- Use `clawfeedradar run` to debug a single source URL and produce one XML/JSON pair.
- Use `clawfeedradar schedule` with a `sources.json` file to run multiple sources periodically, each producing `{label}.xml` + `{label}.json`.
- RSS `<description>` now includes both the preview summary and the full bilingual body for each selected item.
