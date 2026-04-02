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
import logging
from typing import Optional


logger = logging.getLogger("clawfeedradar")


def fetch_fulltext(url: str, *, timeout: int = 300) -> str:
    cmd_tpl = os.environ.get("CLAWFEEDRADAR_SCRAPE_CMD")
    if not cmd_tpl or not url:
        logger.warning("[scrape] missing CLAWFEEDRADAR_SCRAPE_CMD or URL: url=%r", url)
        return ""

    # Simple template: append URL as last argument.
    # If users need more control, they can write a wrapper script.
    cmd = f"{cmd_tpl} {shlex.quote(url)}"

    max_retries = 3
    backoff_min = 5.0
    backoff_max = 10.0

    last_exc = None
    for attempt in range(1, max_retries + 1):
        logger.info("[scrape] fetching fulltext (attempt %d/%d): %s", attempt, max_retries, url)
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                text=True,
            )
        except subprocess.TimeoutExpired as e:
            last_exc = e
            logger.warning("[scrape] timeout after %ds for URL: %s", timeout, url)
        except Exception as e:
            last_exc = e
            logger.error("[scrape] error running command for URL %s: %s", url, e)
        else:
            if proc.returncode == 0:
                out = proc.stdout or ""
                if out.strip():
                    logger.info(
                        "[scrape] success, %d characters retrieved for URL: %s", len(out), url
                    )
                    return out
                logger.warning("[scrape] empty output for URL: %s", url)
            else:
                stderr_snippet = (proc.stderr or "")[:200]
                logger.warning(
                    "[scrape] non-zero exit code %d for URL %s, stderr=%r",
                    proc.returncode,
                    url,
                    stderr_snippet,
                )
        if attempt < max_retries:
            import random, time as _time

            delay = random.uniform(backoff_min, backoff_max)
            logger.info("[scrape] retrying URL %s after %.1fs", url, delay)
            _time.sleep(delay)

    logger.error("[scrape] giving up after %d attempts for URL %s: %r", max_retries, url, last_exc)
    return ""
