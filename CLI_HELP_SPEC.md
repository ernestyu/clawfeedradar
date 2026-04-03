# CLI_HELP_SPEC (snapshot of --help output)

```text
usage: clawfeedradar [-h] {demo,run,schedule} ...

Personal feed radar based on clawsqlite.

positional arguments:
  {demo,run,schedule}
    demo               Run a demo scoring pipeline with fake candidates
    run                Run radar on a single source URL and write a RSS XML +
                       JSON
    schedule           Scan sources.json and run per-source radar when due

options:
  -h, --help           show this help message and exit
```


## run

```text
usage: clawfeedradar run [-h] [--root ROOT] --url URL [--output OUTPUT]
                         [--score-threshold SCORE_THRESHOLD]
                         [--max-items MAX_ITEMS] [--source-lang SOURCE_LANG]
                         [--target-lang TARGET_LANG] [--json]

options:
  -h, --help            show this help message and exit
  --root ROOT           clawsqlite knowledge root (overrides CLAWSQLITE_ROOT)
  --url URL             Single feed URL (RSS/HN/etc.) to pull candidates from
  --output OUTPUT       RSS XML output path (default:
                        $CLAWFEEDRADAR_OUTPUT_DIR/radar.xml)
  --score-threshold SCORE_THRESHOLD
                        minimum interest_score to keep a candidate
  --max-items MAX_ITEMS
                        maximum number of items in the feed (overrides
                        CLAWFEEDRADAR_MAX_ITEMS or default 12)
  --source-lang SOURCE_LANG
                        source language hint for LLM (e.g. en, auto by
                        default)
  --target-lang TARGET_LANG
                        target language for summaries/translation (e.g. zh)
  --json                also print selected items as JSON to stdout
```


## schedule

```text
usage: clawfeedradar schedule [-h] [--root ROOT] [--sources-json SOURCES_JSON]
                              [--output-dir OUTPUT_DIR]

options:
  -h, --help            show this help message and exit
  --root ROOT           clawsqlite knowledge root (overrides CLAWSQLITE_ROOT)
  --sources-json SOURCES_JSON
                        sources.json path (overrides
                        CLAWFEEDRADAR_SOURCES_JSON)
  --output-dir OUTPUT_DIR
                        Output directory for per-source feeds (default:
                        $CLAWFEEDRADAR_OUTPUT_DIR or ./feeds)
```
