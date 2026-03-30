# -*- coding: utf-8 -*-
from __future__ import annotations

"""Generic RSS/Atom source adapter for clawfeedradar.

Each line in sources.txt can point to an RSS/Atom feed. This adapter:
- parses the feed with feedparser
- turns entries into Candidate objects
- infers a coarse "source" field from the entry link (hackernews/arxiv/rss, etc.)
"""

from datetime import datetime, timezone
from typing import List
from urllib.parse import urlparse

import feedparser

from ..models import Candidate


def _safe_int(s, default: int = 0) -> int:
    try:
        return int(s)
    except Exception:
        return default


def _infer_source_from_link(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return "rss"

    if "news.ycombinator.com" in host or "hnrss.org" in host:
        return "hackernews"
    if "arxiv.org" in host:
        return "arxiv"
    return "rss"


def _parse_datetime(entry) -> datetime:
    # feedparser normalizes published_parsed/updated_parsed into time.struct_time
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, key, None)
        if t is not None:
            try:
                return datetime.fromtimestamp(
                    # struct_time -> timestamp
                    int(datetime(*t[:6], tzinfo=timezone.utc).timestamp()),
                    tz=timezone.utc,
                )
            except Exception:
                continue
    return datetime.now(timezone.utc)


def fetch_candidates_from_rss(source_url: str, *, max_items: int = 100) -> List[Candidate]:
    d = feedparser.parse(source_url)
    entries = d.entries or []

    out: List[Candidate] = []
    for e in entries[:max_items]:
        link = getattr(e, "link", None) or ""
        title = getattr(e, "title", None) or ""
        if not link or not title:
            continue

        summary = getattr(e, "summary", None) or getattr(e, "description", None) or ""
        # tags/keywords -> comma-separated string
        tags_list = []
        for t in getattr(e, "tags", []) or []:
            term = getattr(t, "term", None)
            if term:
                tags_list.append(str(term))
        tags = ",".join(tags_list)

        source = _infer_source_from_link(link)
        published_at = _parse_datetime(e)

        # Popularity: source-specific best-effort signals, fallback to neutral 0.5
        pop = 0.5
        hn_points = None
        hn_comments = None
        if source == "hackernews":
            # hnrss 通常在标题或 summary 中包含 "123 points | 45 comments" 之类格式
            text = " ".join([title, summary])
            try:
                import re

                m = re.search(r"(\d+)\s+points?\s*\|\s*(\d+)\s+comments?", text)
                if m:
                    hn_points = _safe_int(m.group(1), 0)
                    hn_comments = _safe_int(m.group(2), 0)
                    # 简单归一化到 0..1 区间
                    pop = min(1.0, 0.5 * (hn_points / 500.0) + 0.5 * (hn_comments / 100.0))
            except Exception:
                pass

        source_meta = {
            "feed_title": getattr(d.feed, "title", None),
            "feed_url": source_url,
        }
        if hn_points is not None:
            source_meta["hn_points"] = hn_points
        if hn_comments is not None:
            source_meta["hn_comments"] = hn_comments

        cid = getattr(e, "id", None) or link
        out.append(
            Candidate(
                id=str(cid),
                url=link,
                title=title,
                summary=summary,
                tags=tags,
                source=source,
                published_at=published_at,
                popularity_score=pop,
                source_meta=source_meta,
            )
        )

    return out
