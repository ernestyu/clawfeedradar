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
                         [--max-items MAX_ITEMS]
                         [--max-source-items MAX_SOURCE_ITEMS]
                         [--w-recency W_RECENCY] [--w-popularity W_POPULARITY]
                         [--feed-title FEED_TITLE] [--source-lang SOURCE_LANG]
                         [--target-lang TARGET_LANG] [--no-preview]
                         [--preview-words PREVIEW_WORDS] [--json]

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
  --max-source-items MAX_SOURCE_ITEMS
                        max entries to pull from source feed before scoring
                        (only for run)
  --w-recency W_RECENCY
                        per-run recency bias weight (overrides default)
  --w-popularity W_POPULARITY
                        per-run popularity bias weight (overrides default)
  --feed-title FEED_TITLE
                        RSS channel title for this run (default:
                        clawfeedradar)
  --source-lang SOURCE_LANG
                        source language hint for LLM (e.g. en, auto by
                        default)
  --target-lang TARGET_LANG
                        target language for summaries/translation (e.g. zh)
  --no-preview          disable preview summary LLM (debug/fast mode)
  --preview-words PREVIEW_WORDS
                        target length for preview summary in words (run mode)
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
