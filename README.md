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

   所有数据源最终汇总为统一的「候选文章 JSON 格式」，形如：

   ```json
   {
     "id": "hn-123456",
     "url": "https://example.com/post",
     "title": "Efficient vector search with SQLite",
     "summary": "短摘要或抓取到的正文片段",
     "tags": "hn,sqlite,vector,search",
     "source": "hackernews",
     "points": 350,
     "created_at": "2026-03-28T07:00:00Z"
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
       --score-threshold 0.9 \
       --output ./feeds/hn_radar.xml
     ```

     内部步骤：

     1. 从各个源（HN / RSS / arXiv）拉候选文章；
     2. 对每条候选，调用 `clawsqlite knowledge score-candidates` 获取兴趣分；
     3. 过滤掉兴趣分不足的文章，只保留“高度落在兴趣簇里”的部分；
     4. 用小型 LLM（或你配置的翻译服务）生成中英摘要；
     5. 把这些条目渲染成一个 RSS feed (`*.xml`) 文件。

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
