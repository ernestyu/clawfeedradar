# CLI_HELP_SPEC_MANSTYLE (man-style CLI reference)

## NAME

clawfeedradar - personal feed radar based on clawsqlite

## SYNOPSIS

clawfeedradar demo
clawfeedradar run [--root ROOT] --url URL [--output OUTPUT]
                  [--score-threshold SCORE_THRESHOLD]
                  [--max-items MAX_ITEMS]
                  [--source-lang SOURCE_LANG]
                  [--target-lang TARGET_LANG]
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

clawfeedradar run   --root /path/to/knowledge_data   --url https://hnrss.org/frontpage   --output ./feeds/hn-frontpage.xml   --score-threshold 0.4   --max-items 5


#### run OPTIONS


- `--root ROOT`
  clawsqlite 知识库根目录。若省略，则使用环境变量 `CLAWSQLITE_ROOT`。


- `--url URL`
  单个源 URL（HN RSS / 普通 RSS / 其它），用于拉取候选。


- `--output OUTPUT`
  RSS XML 输出路径。若省略，则为 `$CLAWFEEDRADAR_OUTPUT_DIR/radar.xml`。


- `--score-threshold SCORE_THRESHOLD`
  最低 `interest_score` 门槛，低于该值的候选会被过滤。


- `--max-items MAX_ITEMS`
  最多保留的条目数。优先级为：CLI > 环境变量 `CLAWFEEDRADAR_MAX_ITEMS` > 默认 12。


- `--source-lang SOURCE_LANG`
  传给小 LLM 的源语言提示（例如 `en` / `auto`）。


- `--target-lang TARGET_LANG`
  传给小 LLM 的目标语言（例如 `zh`）。


- `--json`
  除了写 JSON sidecar 外，同时在 stdout 打印本次选中的条目 JSON。


### schedule

根据 `sources.json` 跑多个源，每个源各自产生 `{label}.xml` + `{label}.json`：

clawfeedradar schedule   --root /path/to/knowledge_data   --sources-json /path/to/sources.json   --output-dir ./feeds


`sources.json` 每个条目示例：

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


#### schedule OPTIONS


- `--root ROOT`
  clawsqlite 知识库根目录，规则同 `run`。


- `--sources-json SOURCES_JSON`
  `sources.json` 路径。若省略，则使用 `CLAWFEEDRADAR_SOURCES_JSON`。


- `--output-dir OUTPUT_DIR`
  每个源的 XML/JSON 输出目录。若省略，则使用 `CLAWFEEDRADAR_OUTPUT_DIR` 或当前目录下 `./feeds`。

