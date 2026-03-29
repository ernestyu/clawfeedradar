# -*- coding: utf-8 -*-
from __future__ import annotations

"""Source adapters for clawfeedradar.

v0: 仅实现 Hacker News 适配器，其它源先返回空列表。
"""

from typing import List
from urllib.parse import urlparse

from ..models import Candidate
from .hn import fetch_candidates_from_hn
from .rss import fetch_candidates_from_rss


def detect_source_type(source_url: str) -> str:
    """根据 URL 粗略判断源类型。

    v0 策略：
    - 明确的 RSS/Atom URL → "rss"（包括 hnrss / HN RSS 等）；
    - 未来保留 "hackernews" / "arxiv" 等专用类型。
    """

    try:
        parsed = urlparse(source_url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
    except Exception:
        return "unknown"

    if not parsed.scheme.startswith("http"):
        return "unknown"

    # HN RSS / hnrss 等一律按 RSS 处理，由 RSS 适配器内部再细分 source 字段
    if "hnrss.org" in host or "news.ycombinator.com" in host:
        return "rss"

    # 粗略识别 RSS/Atom
    if path.endswith(".xml") or "rss" in path or "atom" in path or "feed" in path:
        return "rss"

    return "unknown"


def fetch_candidates_from_source(source_type: str, source_url: str) -> List[Candidate]:
    """拉取单个源的候选列表。

    - rss: 通过 feedparser 解析 RSS/Atom feed；
    - hackernews: 保留 API 适配器（目前未通过 sources.txt 暴露）；
    - 其它类型：暂时返回空列表。
    """

    if source_type == "rss":
        return fetch_candidates_from_rss(source_url)

    if source_type == "hackernews":
        return fetch_candidates_from_hn(source_url)

    return []
