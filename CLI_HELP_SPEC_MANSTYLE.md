# CLI_HELP_SPEC_MANSTYLE (man-style CLI reference)

## NAME

clawfeedradar - personal feed radar based on clawsqlite

## SYNOPSIS

clawfeedradar demo
clawfeedradar run [--root ROOT] --url URL [--output OUTPUT]
                  [--score-threshold SCORE_THRESHOLD]
                  [--max-items MAX_ITEMS]
                  [--max-source-items MAX_SOURCE_ITEMS]
                  [--w-recency W_RECENCY] [--w-popularity W_POPULARITY]
                  [--feed-title FEED_TITLE]
                  [--source-lang SOURCE_LANG]
                  [--target-lang TARGET_LANG]
                  [--no-preview]
                  [--preview-words PREVIEW_WORDS]
                  [--json]
clawfeedradar schedule [--root ROOT]
                       [--sources-json SOURCES_JSON]
                       [--output-dir OUTPUT_DIR]

## DESCRIPTION

clawfeedradar 从一个或多个信息源（例如 HN / RSS）抓取候选文章，
利用 clawsqlite 的兴趣簇对候选进行打分和排序，
生成带有中英对照摘要的 RSS/XML + JSON。

## COMMANDS


### demo

运行内置演示流程，使用假的候选数据，用于验证打分/日志管线。


### run

从单个源 URL 抓取候选文章，打分并生成一对 XML + JSON：

clawfeedradar run \
  --root /path/to/knowledge_data \
  --url https://example.com/feed.xml \
  --output ./feeds/example.xml \
  --score-threshold 0.4 \
  --max-items 12


#### run OPTIONS

- `--root ROOT`
  clawsqlite 知识库根目录。若省略，则使用环境变量 `CLAWSQLITE_ROOT`。

- `--url URL`
  单个源 URL（RSS/HN/其它），用于拉取候选。

- `--output OUTPUT`
  RSS XML 输出路径。若省略，则为 `$CLAWFEEDRADAR_OUTPUT_DIR/radar.xml`。

- `--score-threshold SCORE_THRESHOLD`
  最低 `interest_score` 门槛，低于该值的候选会被过滤。

- `--max-items MAX_ITEMS`
  最多保留的条目数。优先级为：CLI > 环境变量 `CLAWFEEDRADAR_MAX_ITEMS` > 默认 12。

- `--max-source-items MAX_SOURCE_ITEMS`
  在打分前，从源 feed 中最多拉取多少条 entry。仅作用于 `run` 模式。

- `--w-recency W_RECENCY`
  本次运行的 recency 偏置权重，覆盖默认配置（或环境变量）。

- `--w-popularity W_POPULARITY`
  本次运行的 popularity 偏置权重，覆盖默认配置（或环境变量）。

- `--feed-title FEED_TITLE`
  本次 RSS 渠道的 `<title>` 文本。若省略，则默认 `clawfeedradar`。

- `--source-lang SOURCE_LANG`
  传给小 LLM 的源语言提示（例如 `en` / `auto`）。

- `--target-lang TARGET_LANG`
  传给小 LLM 的目标语言（例如 `zh`）。

- `--no-preview`
  关闭预览摘要 LLM 调用，仅做打分/排序/抓全文（适合调试）。

- `--preview-words PREVIEW_WORDS`
  预览摘要的目标字数（word 级，而非字符），仅作用于 `run` 模式。

- `--json`
  除了写 JSON sidecar 外，同时在 stdout 打印本次选中的条目 JSON。


### schedule

根据 `sources.json` 跑多个源，每个源各自产生 `{label}.xml` + `{label}.json`：

clawfeedradar schedule \
  --root /path/to/knowledge_data \
  --sources-json /path/to/sources.json \
  --output-dir ./feeds


每个 `sources.json` 条目示例：

```jsonc
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
```


#### schedule OPTIONS

- `--root ROOT`
  clawsqlite 知识库根目录，规则同 `run`。

- `--sources-json SOURCES_JSON`
  `sources.json` 路径。若省略，则使用 `CLAWFEEDRADAR_SOURCES_JSON`。

- `--output-dir OUTPUT_DIR`
  每个源的 XML/JSON 输出目录。若省略，则使用 `CLAWFEEDRADAR_OUTPUT_DIR` 或当前目录下 `./feeds`。
