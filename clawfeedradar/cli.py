# -*- coding: utf-8 -*-
from __future__ import annotations

"""CLI entrypoint for clawfeedradar.

Commands:
- `clawfeedradar demo`：使用假数据验证打分链路；
- `clawfeedradar run`：从 sources 文件拉取候选，跑完整打分并输出单一 XML+JSON；
- `clawfeedradar schedule`：定期扫描 sources.json，为每个源生成独立的 XML+JSON。
"""

import argparse
import os
import sys

from .demo import run_demo
from .runner import run_radar, schedule_from_sources_json


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clawfeedradar", description="Personal feed radar based on clawsqlite.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("demo", help="Run a demo scoring pipeline with fake candidates")
    sp.set_defaults(func=_cmd_demo)

    rp = sub.add_parser("run", help="Run radar on real sources and write a single RSS XML + JSON")
    rp.add_argument("--root", help="clawsqlite knowledge root (overrides CLAWSQLITE_ROOT)")
    rp.add_argument("--sources-file", help="sources.txt path (overrides CLAWFEEDRADAR_SOURCES_FILE)")
    rp.add_argument("--output", help="RSS XML output path (default: $CLAWFEEDRADAR_OUTPUT_DIR/radar.xml)")
    rp.add_argument("--score-threshold", type=float, default=0.0, help="minimum interest_score to keep a candidate")
    rp.add_argument("--max-items", type=int, default=12, help="maximum number of items in the feed")
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
    sources_file = args.sources_file or os.environ.get("CLAWFEEDRADAR_SOURCES_FILE")
    if not sources_file:
        raise SystemExit("--sources-file or CLAWFEEDRADAR_SOURCES_FILE is required")

    output_xml = args.output
    if not output_xml:
        out_dir = os.environ.get("CLAWFEEDRADAR_OUTPUT_DIR", os.path.join(os.getcwd(), "feeds"))
        output_xml = os.path.join(out_dir, "radar.xml")

    return run_radar(
        root=root,
        sources_file=sources_file,
        output_xml=output_xml,
        score_threshold=float(args.score_threshold or 0.0),
        max_items=int(args.max_items or 0),
        json_stdout=bool(args.json),
        source_lang=args.source_lang,
        target_lang=args.target_lang,
    )


def _cmd_schedule(args) -> int:
    root = args.root or os.environ.get("CLAWSQLITE_ROOT")
    sources_json = args.sources_json or os.environ.get("CLAWFEEDRADAR_SOURCES_JSON")
    if not sources_json:
        raise SystemExit("--sources-json or CLAWFEEDRADAR_SOURCES_JSON is required")

    output_dir = args.output_dir
    if not output_dir:
        output_dir = os.environ.get("CLAWFEEDRADAR_OUTPUT_DIR", os.path.join(os.getcwd(), "feeds"))

    return schedule_from_sources_json(
        root=root,
        sources_json_path=sources_json,
        output_dir=output_dir,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
