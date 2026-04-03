# clawfeedradar

个人「读报雷达」：围绕 `clawsqlite` 知识库，从 Hacker News / RSS / arXiv 等信息源中筛选**你可能感兴趣**的文章，自动生成带中英对照摘要的专属 RSS feed。

> 设计目标：简单可控、行为可审计、和 `clawsqlite` 解耦。当前文档描述的是 v0 实现，后续在保持接口稳定的前提下演进。

---

## 简介

clawfeedradar 的核心工作是三件事：

1. **从外部源抓候选文章**  
   支持 HN RSS、普通 RSS，后续可扩展 arXiv 等。
2. **利用 clawsqlite 的兴趣簇做打分排序**  
   通过 Embedding + 兴趣簇，算出每篇候选文章和「你的长期兴趣」的匹配度，并结合时间和热度打一个综合分。
3. **生成带中英对照摘要的 RSS feed**  
   对选中的文章抓全文，调用小 LLM 生成中英对照正文，并写出一对 XML（给 RSS Reader）+ JSON（调试/二次开发）。

一句话：**clawsqlite 负责“知道你喜欢什么”，clawfeedradar 负责“去外面帮你找类似的东西，翻译好喂给你的 RSS 阅读器”。**

---

## 快速开始

### 1. 准备环境

假设你的目录结构类似：

```text
~/.openclaw/workspace/
  ├── clawsqlite          # clawsqlite 仓库（已有）
  ├── knowledge_data      # 你的 clawsqlite-knowledge 知识库
  ├── clawfeedradar       # 本仓库
  └── clawfetch / ...     # 抓全文用的 clawfetch + wrapper
```

先确保 clawsqlite 这边已经有一个知识库（articles + 向量 + 兴趣簇）：

```bash
cd ~/.openclaw/workspace/clawsqlite
clawsqlite knowledge build-interest-clusters   --root ~/.openclaw/workspace/knowledge_data
```

### 2. 配置 clawfeedradar

在 `clawfeedradar` 目录下：

```bash
cd ~/.openclaw/workspace/clawfeedradar
cp ENV.example .env
# 打开 .env 按你的环境改一遍
```

最小可工作配置（核心几类）：

- clawsqlite 知识库
  - `CLAWSQLITE_ROOT` 指向 `knowledge_data` 根目录
  - `CLAWSQLITE_DB` 如有需要显式覆盖 sqlite 路径
- Embedding 服务（与 clawsqlite 共用）
  - `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_API_KEY`
  - `CLAWSQLITE_VEC_DIM` 必须与 embedding 模型维度一致
- 输出目录
  - `CLAWFEEDRADAR_OUTPUT_DIR`：生成的 XML/JSON 放在哪个目录
- 小 LLM（摘要 + 中英对照）
  - `SMALL_LLM_BASE_URL` / `SMALL_LLM_MODEL` / `SMALL_LLM_API_KEY`
  - `CLAWFEEDRADAR_LLM_MAX_OUTPUT_CHARS`：单次调用的输出字符预算
  - `CLAWFEEDRADAR_LLM_SLEEP_BETWEEN_MS`：两次调用之间的 sleep（毫秒）
  - `CLAWFEEDRADAR_LLM_SOURCE_LANG` / `CLAWFEEDRADAR_LLM_TARGET_LANG`
  - `CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS`：每个中英对照段的最大字符数（按屏拆分），例如 2400 ≈ 600 字
- 抓全文
  - `CLAWFEEDRADAR_SCRAPE_CMD`：一个接受 URL、输出 markdown 的命令，通常是调用 `clawfetch` 的 wrapper：
    - 例如：`/home/node/.openclaw/workspace/clawfetch_wrapper.sh "$URL"`
  - `CLAWFEEDRADAR_SCRAPE_WORKERS`：抓取并发 worker 数（同 host 内仍串行）
- 打分权重
  - `CLAWFEEDRADAR_W_SIM_BEST`
  - `CLAWFEEDRADAR_W_SIM_SECOND`
  - `CLAWFEEDRADAR_W_RECENCY`
  - `CLAWFEEDRADAR_W_POPULARITY`
  - `CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS`：recency 半衰期（天）
- 默认条目数
  - `CLAWFEEDRADAR_MAX_ITEMS`：不写 `--max-items` 时，每轮最多选几条

### 3. 跑一轮 HN 前页（单源调试）

```bash
cd ~/.openclaw/workspace/clawfeedradar
python -m clawfeedradar.cli run   --root ~/.openclaw/workspace/orgmode/clawsqlite/data   --url https://hnrss.org/frontpage   --output ./feeds/hn-frontpage.xml   --score-threshold 0.4   --max-items 5   --source-lang en   --target-lang zh
```

跑完后你会得到：

- `./feeds/hn-frontpage.xml`  
  一个 RSS feed，适合直接在 RSS 阅读器里订阅；每条 `<description>` 包含：
  - preview 短摘要（`summary_preview`）
  - 全文中英对照正文（`body_bilingual`）
- `./feeds/hn-frontpage.json`  
  同名 JSON sidecar，包含：
  - 原始 fulltext
  - `summary_preview`
  - `body_bilingual`
  - 打分明细（interest_score / final_score / sim_best / sim_second / best_cluster_id 等）

---

## 配置

### 1. 环境变量（.env）

完整列表参见 `ENV.example`，这里只强调关键点：

#### clawsqlite 相关

```env
CLAWSQLITE_ROOT=/home/node/.openclaw/workspace/orgmode/clawsqlite/data
CLAWSQLITE_DB=/home/node/.openclaw/workspace/orgmode/clawsqlite/data/clawkb.sqlite3
```

#### Embedding 相关

```env
EMBEDDING_BASE_URL=https://embed.example.com/v1
EMBEDDING_MODEL=your-embedding-model
EMBEDDING_API_KEY=sk-your-embedding-key
CLAWSQLITE_VEC_DIM=1024
```

#### clawfeedradar 输出与抓取

```env
CLAWFEEDRADAR_OUTPUT_DIR=/home/node/.openclaw/workspace/clawfeedradar/feeds
CLAWFEEDRADAR_SCRAPE_CMD="/home/node/.openclaw/workspace/clawfetch_wrapper.sh"
CLAWFEEDRADAR_SCRAPE_WORKERS=4
# CLAWFEEDRADAR_HTTP_USER_AGENT=...
```

#### 打分权重

```env
CLAWFEEDRADAR_W_SIM_BEST=0.6
CLAWFEEDRADAR_W_SIM_SECOND=0.2
CLAWFEEDRADAR_W_RECENCY=0.1
CLAWFEEDRADAR_W_POPULARITY=0.1
CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS=3
```

- `sim_best`：候选与最近兴趣簇的相似度；
- `border = sim_second * (1 - sim_best)`：表示“在两个簇之间”的边缘程度；
- `recency`：按半衰期计算的时间权重；
- `popularity_score`：源侧归一化的热度；
- 最终兴趣分：

  ```text
  interest = W_SIM_BEST   * sim_best
           + W_SIM_SECOND * border
           + W_RECENCY    * recency
           + W_POPULARITY * popularity_score
  ```

#### 小 LLM 相关

```env
SMALL_LLM_BASE_URL=http://zl.moonsetz.com:18099/v1
SMALL_LLM_MODEL=Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf
SMALL_LLM_API_KEY=sk-...

CLAWFEEDRADAR_LLM_MAX_OUTPUT_CHARS=6000
CLAWFEEDRADAR_LLM_SLEEP_BETWEEN_MS=500
CLAWFEEDRADAR_LLM_SOURCE_LANG=auto
CLAWFEEDRADAR_LLM_TARGET_LANG=zh
# 每个中英对照段的最大字符数（一屏）
# CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS=2400
```

#### 默认条目数

```env
# 不写 --max-items 时的默认值
CLAWFEEDRADAR_MAX_ITEMS=5
```

---

## 用法

### 1. `clawfeedradar run` — 单源模式

从单个源 URL 抓取候选，打分并输出一对 XML + JSON。

```bash
python -m clawfeedradar.cli run   --root /path/to/knowledge_data   --url https://hnrss.org/frontpage   --output ./feeds/hn-frontpage.xml   --score-threshold 0.4   --max-items 5   --source-lang en   --target-lang zh
```

参数说明：

- `--root`：clawsqlite 知识库根目录（优先级：CLI > `CLAWSQLITE_ROOT`）。
- `--url`：单个源 URL（HN RSS / 普通 RSS / 未来可扩展其它）。
- `--output`：RSS XML 输出路径，默认 `$CLAWFEEDRADAR_OUTPUT_DIR/radar.xml`。
- `--score-threshold`：最低 `interest_score` 门槛，低于该值的候选会被过滤。
- `--max-items`：本轮最多保留多少条；优先级：CLI > `CLAWFEEDRADAR_MAX_ITEMS` > 默认 12。
- `--source-lang` / `--target-lang`：传给小 LLM 的语言提示。
- `--json`：额外在 stdout 打印选中条目的 JSON（调试用）。

### 2. `clawfeedradar schedule` — 多源调度

根据 `sources.json` 跑多个源，每个源各自产生 `{label}.xml` + `{label}.json`。

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

命令示例：

```bash
python -m clawfeedradar.cli schedule   --root /path/to/knowledge_data   --sources-json /path/to/sources.json   --output-dir /path/to/feeds
```

行为：

- 遍历 `sources.json` 的每个 entry：
  - 若 `last_success_at` 为空或距离现在超过 `interval_hours` 小时，则判定为 due；
  - 调同样的 pipeline（与 `run` 一致），最多选 `max_entries` 条；
  - 输出 `{label}.xml` + `{label}.json` 到 `output-dir`；
  - 更新该 entry 的 `last_success_at` / `last_error`。

约定：

- `score_threshold` 只在 `sources.json` 中配置，不在 .env 里重复；
- `max_entries` 是 schedule 专用的 per-source 上限，不和 `CLAWFEEDRADAR_MAX_ITEMS` 混用。

---

## 行为细节

这一节用于描述实现上的关键行为，方便你审计和调参。

### 1. 抓全文并发模型

- 默认使用 `CLAWFEEDRADAR_SCRAPE_WORKERS` 个线程（例如 4）。
- **按 host 串行、跨 host 并行**：
  - 每个 host 有一把锁，同一 host 的 URL 会在 `with lock:` 下逐一抓取；
  - 不同 host 之间可以并发运行，最多 `SCRAPE_WORKERS` 个并行抓取。
- 日志会写出本轮抓取的 host 分布：

  ```text
  [pipeline] fulltext fetch: urls=100, hosts=1, max_workers=4
  [pipeline] single host 'arxiv.org' detected; requests to this host are serialized via host-level locks
  ```

  便于你确认像 arxiv 这种是不是“老老实实串行抓”。

### 2. 打分与选条

- 对每个候选：
  - 用 embedding 服务生成向量；
  - 与兴趣簇计算 `sim_best` / `sim_second`；
  - 计算边缘度：`border = sim_second * (1 - sim_best)`；
  - 计算 `recency`（按 `RECENCY_HALF_LIFE_DAYS` 做指数衰减）；
  - 取源适配器提供的 `popularity_score`（0..1）。
- 最终兴趣分：

  ```text
  interest = W_SIM_BEST   * sim_best
           + W_SIM_SECOND * border
           + W_RECENCY    * recency
           + W_POPULARITY * popularity_score
  ```

- 选条逻辑：
  - 先对所有候选算分，得到 `scored` 列表；
  - 过滤：`interest_score >= score_threshold`；
  - 按 `final_score` 排序；
  - 取前 `max_items` 条作为 `selected`；
  - 只对 `selected` 调 LLM；
  - 日志记录：

    ```text
    [pipeline] scored=20, passed_threshold=15, max_items=5, selected=5 (score_threshold=0.400)
    ```

### 3. LLM 行为

- 预览摘要：
  - 输入为构造好的长摘要（约 1200 字 + 最后一段），不再额外截断；
  - 输出为目标语言的短摘要，用于 RSS `<description>` 的开头部分。
- 中英对照正文：
  - 以全文为输入；
  - 先按空行切自然段，再按 `CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS` 把过长段落按句号等切成“屏段”；
  - 对每篇文章的所有屏段，按字符预算分成若干批请求 LLM；
  - 支持部分成功 + 最多 3 轮重试；
  - 输出为“原文屏段 + 译文屏段”交替的 markdown 文本。

### 4. XML / JSON 输出约定

- `run`：
  - `--output /path/to/foo.xml` → JSON sidecar 在 `/path/to/foo.json`；
- `schedule`：
  - 源的 `label` 为 `bar` → 输出 `bar.xml` + `bar.json`；
- JSON 中字段：
  - `fulltext` / `summary_preview` / `body_bilingual`；
  - 各种打分明细；
- XML 中 `<item><description>`：

  ```text
  description = summary_preview + "

" + body_bilingual
  ```

  若某一项不存在，则退回到剩下的那一项。

---

## 设计

更细节的架构、打分公式推导、与 clawsqlite 的边界设计，见：

- `docs/DESIGN.md`
- `docs/SPEC.md`

当前实现与 SPEC 中的 v0 设计保持一致：

- clawfeedradar 不调用 clawsqlite 的内部 Python API，只通过已定义的 DB schema + embedding/LLM 服务接口工作；
- 打分主通道是源无关的兴趣空间，源特化逻辑仅通过 `popularity_score` 和少量 `source_meta` 参与（当前 v0 只用了通用热度）；
- 选条逻辑采用简单 top-N，避免 early-stage 过度复杂的 explore slot；多样性主要由 `W_SIM_SECOND * border` 提供。

---

## TODO

- [ ] 更丰富的数据源适配器：直接支持 HN API / arxiv API 等，不只靠 RSS。
- [ ] 更细粒度的打分分析输出（例如 per-cluster 贡献）。
- [ ] 为 feed 输出增加「解释字段」（为什么推荐这条）。
- [ ] 拆分中英文 README：
  - `README_zh.md`：中文完整说明；
  - `README.md`：英文简版 + 链接到中文说明。
- [ ] 根据实际使用反馈调整 scoring / LLM / 抓取策略，整理成稳定的 v1 规格。
