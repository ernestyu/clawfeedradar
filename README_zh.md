# clawfeedradar

个人「读报雷达」：围绕 `clawsqlite` 知识库，从 Hacker News / RSS / arXiv 等信息源中筛选**你可能感兴趣**的文章，自动生成带中英对照摘要的专属 RSS feed。

> 设计目标：简单可控、行为可审计、和 `clawsqlite` 解耦。本文档描述的是 **当前 v1 实现**（分支 `bot/20260402-embedding`）。

---

## 在 OpenClaw 里使用时

clawfeedradar 假定你已经有一个带兴趣簇的 `clawsqlite` 知识库。

在 OpenClaw 工作区里，推荐的方式是：

1. **先安装 `clawsqlite-knowledge` 这个 skill（如果还没装）**

   ```bash
   openclaw skills add clawsqlite-knowledge
   ```

   或者通过网页目录：

   - <https://clawhub.ai/skills/clawsqlite-knowledge>

2. **按照该 skill 的 README 初始化知识库并构建兴趣簇**  
   （例如：导入 org-mode/markdown 文章、跑 `build-interest-clusters` 等）。

一旦 `clawsqlite-knowledge` 把 `interest_clusters` 建好了，clawfeedradar 就可以直接挂在同一个 DB 上，把兴趣空间复用起来做打分。

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

## 通过 Git 发布 feeds（GitHub Pages / Gitee Pages）

clawfeedradar 在写完本地 XML/JSON 后，可以选择性地将这些文件 push 到一个 git 仓库，
配合 GitHub Pages 或 Gitee Pages 免费托管你的 RSS feed。

### 1）GitHub Pages 示例

1. 在 GitHub 上创建一个公开仓库，例如：`github.com/yourname/clawfeedradar-feed`，
   在 Settings → Pages 中启用 Pages 功能，选择：

   - Branch：`gh-pages`
   - Directory：`/` 或 `feeds/`（下面示例使用 `feeds/`）

2. 在运行 clawfeedradar 的环境中，配置好访问 GitHub 的 git 认证：

   - SSH URL：配置好 `git@github.com` 的 SSH key；
   - 或 HTTPS URL：配置好 PAT / credential helper。

3. 在 `.env` 中添加：

   ```env
   CLAWFEEDRADAR_PUBLISH_GIT_REPO=git@github.com:yourname/clawfeedradar-feed.git
   CLAWFEEDRADAR_PUBLISH_GIT_BRANCH=gh-pages
   CLAWFEEDRADAR_PUBLISH_GIT_PATH=feeds
   ```

4. 之后每次运行 `clawfeedradar run` / `schedule`：

   - clawfeedradar 会在本地 `./.publish/yourname-clawfeedradar-feed/` 下维护一个 clone；
   - 把生成的 `*.xml` / `*.json` 拷贝到该 clone 的 `feeds/` 目录；
   - 自动执行 `git add` / `git commit` / `git push`。

5. 最终订阅地址类似于：

   ```text
   https://yourname.github.io/clawfeedradar-feed/feeds/bbc-tech.xml
   ```

### 2）Gitee Pages 示例（适合国内网络）

对 Gitee，流程几乎一样，只是远端换成 `gitee.com`：

1. 在 Gitee 上创建一个仓库，例如：`gitee.com/yourname/clawfeedradar-feed`，
   在 Gitee Pages 设置里启用 Pages，并选择合适的分支/目录（例如 `gh-pages` / `feeds/`）。

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

如果未配置 `CLAWFEEDRADAR_PUBLISH_GIT_REPO`，clawfeedradar 只会在本地写 XML/JSON，
不会尝试推送远端仓库。

---

（其余章节与英文 README.md 对应，主要是打分/LLM/CLI 行为，这里不重复。）
