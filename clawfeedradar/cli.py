# -*- coding: utf-8 -*-
from __future__ import annotations

"""CLI entrypoint for clawfeedradar.

v0: 提供一个 `clawfeedradar demo` 命令，用假数据验证打分链路。
后续会扩展为 `clawfeedradar run`，接真实源和 RSS 输出。
"""

import argparse
import sys

from .demo import run_demo


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clawfeedradar", description="Personal feed radar based on clawsqlite.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("demo", help="Run a demo scoring pipeline with fake candidates")
    sp.set_defaults(func=_cmd_demo)

    return p


def _cmd_demo(args) -> int:
    return run_demo()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
