# -*- coding: utf-8 -*-
from __future__ import annotations

"""Fulltext scraping helper.

v0: call an external command defined by CLAWFEEDRADAR_SCRAPE_CMD.
The command should accept a URL and emit markdown/plaintext to stdout.

Recommended implementation: a small shell wrapper around the clawfetch skill.
"""

import os
import shlex
import subprocess
from typing import Optional


def fetch_fulltext(url: str, *, timeout: int = 300) -> str:
    cmd_tpl = os.environ.get("CLAWFEEDRADAR_SCRAPE_CMD")
    if not cmd_tpl or not url:
        return ""

    # Simple template: append URL as last argument.
    # If users need more control, they can write a wrapper script.
    cmd = f"{cmd_tpl} {shlex.quote(url)}"

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            text=True,
        )
    except Exception:
        return ""

    if proc.returncode != 0:
        return ""

    return proc.stdout or ""
