# -*- coding: utf-8 -*-
from __future__ import annotations

"""Source adapters for clawfeedradar.

v0: 仅实现 Hacker News 适配器，其它源先返回空列表。
"""

from typing import List
from urllib.parse import urlparse

from ..models import Candidate
from .hn import fetch_candidates_from_hn


def detect_source_type(source_url: str) -> str:
    """根据 URL 粗略判断源类型。

    目前只识别 Hacker News，其它一律标记为 "unknown"。
    """

    try:
        host = urlparse(source_url).netloc.lower()
    except Exception:
        return "unknown"

    if "news.ycombinator.com" in host:
        return "hackernews"

    # 预留 arxiv / RSS 等类型
    return "unknown"


def fetch_candidates_from_source(source_type: str, source_url: str) -> List[Candidate]:
    """拉取单个源的候选列表。

    - hackernews: 使用官方 API 获取 top/new stories。
    - 其它类型：暂时返回空列表。
    """

    if source_type == "hackernews":
        return fetch_candidates_from_hn(source_url)

    return []
