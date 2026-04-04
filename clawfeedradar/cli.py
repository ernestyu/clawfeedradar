# -*- coding: utf-8 -*-
from __future__ import annotations

"""CLI entrypoint for clawfeedradar.

Commands:
- `clawfeedradar demo`：使用假数据验证打分链路；
- `clawfeedradar run`：从 sources 文件拉取候选，跑完整打分并输出单一 XML+JSON；
- `clawfeedradar schedule`：定期扫描 sources.json，为每个源生成独立的 XML+JSON。
"""

import argparse
import logging
import os
import sys

from .demo import run_demo
from .runner import run_radar, schedule_from_sources_json
from .config import load_project_env
from pathlib import Path

load_project_env()


ROOT_DIR = Path(__file__).resolve().parents[1]


_def_log_initialized = False


def _setup_logging() -> None:
    """Setup file-based logging for clawfeedradar.

    - Log level from CLAWFEEDRADAR_LOG_LEVEL (default: INFO).
    - Log file: <repo_root>/logs/clawfeedradar.log
    - Attach same handler to `clawfeedradar` and `httpx` loggers.
    """
    global _def_log_initialized
    if _def_log_initialized:
        return
    _def_log_initialized = True

    level_name = os.environ.get("CLAWFEEDRADAR_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logs_dir = ROOT_DIR / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / 'clawfeedradar.log'

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    fh = logging.FileHandler(log_path, encoding='utf-8')
    fh.setFormatter(formatter)

    app_logger = logging.getLogger('clawfeedradar')
    app_logger.setLevel(level)
    app_logger.propagate = False
    if not any(isinstance(h, logging.FileHandler) and getattr(h, 'baseFilename', None) == str(log_path) for h in app_logger.handlers):
        app_logger.addHandler(fh)

    httpx_logger = logging.getLogger('httpx')
    httpx_logger.setLevel(level)
    httpx_logger.propagate = False
    if not any(isinstance(h, logging.FileHandler) and getattr(h, 'baseFilename', None) == str(log_path) for h in httpx_logger.handlers):
        httpx_logger.addHandler(fh)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clawfeedradar", description="Personal feed radar based on clawsqlite.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("demo", help="Run a demo scoring pipeline with fake candidates")
    sp.set_defaults(func=_cmd_demo)

    rp = sub.add_parser("run", help="Run radar on a single source URL and write a RSS XML + JSON")
    rp.add_argument("--root", help="clawsqlite knowledge root (overrides CLAWSQLITE_ROOT)")
    rp.add_argument("--url", required=True, help="Single feed URL (RSS/HN/etc.) to pull candidates from")
    rp.add_argument("--output", help="RSS XML output path (default: $CLAWFEEDRADAR_OUTPUT_DIR/radar.xml)")
    rp.add_argument("--score-threshold", type=float, default=0.0, help="minimum interest_score to keep a candidate")
    rp.add_argument("--max-items", type=int, default=None, help="maximum number of items in the feed (overrides CLAWFEEDRADAR_MAX_ITEMS or default 12)")
    rp.add_argument("--max-source-items", type=int, default=None, help="max entries to pull from source feed before scoring (only for run)")
    rp.add_argument("--w-recency", type=float, default=None, help="per-run recency bias weight (overrides default)")
    rp.add_argument("--w-popularity", type=float, default=None, help="per-run popularity bias weight (overrides default)")
    rp.add_argument("--feed-title", help="RSS channel title for this run (default: clawfeedradar)")
    rp.add_argument("--source-lang", help="source language hint for LLM (e.g. en, auto by default)")
    rp.add_argument("--target-lang", help="target language for summaries/translation (e.g. zh)")
    rp.add_argument("--json", action="store_true", help="also print selected items as JSON to stdout")
    rp.set_defaults(func=_cmd_run)

    sp = sub.add_parser("schedule", help="Scan sources.json and run per-source radar when due")
    sp.add_argument("--root", help="clawsqlite knowledge root (overrides CLAWSQLITE_ROOT)")
    sp.add_argument("--sources-json", help="sources.json path (overrides CLAWFEEDRADAR_SOURCES_JSON)")
    sp.add_argument("--output-dir", help="Output directory for per-source feeds (default: $CLAWFEEDRADAR_OUTPUT_DIR or ./feeds)")
    sp.set_defaults(func=_cmd_schedule)

    return p


def _cmd_demo(args) -> int:
    return run_demo()


def _cmd_run(args) -> int:
    root = args.root or os.environ.get("CLAWSQLITE_ROOT")
    url = args.url

    output_xml = args.output
    if not output_xml:
        out_dir = os.environ.get("CLAWFEEDRADAR_OUTPUT_DIR", os.path.join(os.getcwd(), "feeds"))
        output_xml = os.path.join(out_dir, "radar.xml")

    # max_items: CLI > env > default 12
    if args.max_items is not None:
        max_items = int(args.max_items)
    else:
        try:
            max_items = int(os.environ.get("CLAWFEEDRADAR_MAX_ITEMS", "12") or "12")
        except Exception:
            max_items = 12

    # max_source_items: CLI-only; how many entries to pull from the source feed before scoring.
    if args.max_source_items is not None:
        max_source_items = int(args.max_source_items)
    else:
        max_source_items = 0

    from .scoring import load_score_params_from_env, ScoreParams
    base_params = load_score_params_from_env()
    if args.w_recency is not None:
        base_params.w_recency = float(args.w_recency)
    if args.w_popularity is not None:
        base_params.w_popularity = float(args.w_popularity)

    rc = run_radar(
        root=root,
        url=url,
        output_xml=output_xml,
        feed_title=args.feed_title,
        score_threshold=float(args.score_threshold or 0.0),
        max_items=max_items,
        json_stdout=bool(args.json),
        max_source_items=max_source_items,
        score_params=base_params,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
    )
    if rc == 0:
        print(f"[run] radar completed successfully for URL {url!r}, output={output_xml}")
    else:
        print(f"[run] radar exited with code {rc} for URL {url!r}, output={output_xml}")
    return rc


def _cmd_schedule(args) -> int:
    root = args.root or os.environ.get("CLAWSQLITE_ROOT")
    sources_json = args.sources_json or os.environ.get("CLAWFEEDRADAR_SOURCES_JSON")
    if not sources_json:
        raise SystemExit("--sources-json or CLAWFEEDRADAR_SOURCES_JSON is required")

    output_dir = args.output_dir
    if not output_dir:
        output_dir = os.environ.get("CLAWFEEDRADAR_OUTPUT_DIR", os.path.join(os.getcwd(), "feeds"))

    rc = schedule_from_sources_json(
        root=root,
        sources_json_path=sources_json,
        output_dir=output_dir,
    )
    if rc == 0:
        print(f"[schedule] radar schedule completed successfully from {sources_json!r}, output_dir={output_dir}")
    else:
        print(f"[schedule] radar schedule exited with code {rc} from {sources_json!r}, output_dir={output_dir}")
    return rc


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
