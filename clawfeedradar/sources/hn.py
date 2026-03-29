# -*- coding: utf-8 -*-
from __future__ import annotations

"""Hacker News source adapter for clawfeedradar.

- 使用 Hacker News 官方 Firebase API
- 将故事转换为内部 Candidate 模型
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

import httpx

from ..models import Candidate


_HN_API_BASE = "https://hacker-news.firebaseio.com/v0"


@dataclass
class _HNConfig:
    list_kind: str = "topstories"  # or "newstories" 等
    max_items: int = 100


def _parse_hn_story(item: dict) -> Candidate | None:
    if not item:
        return None
    if item.get("type") != "story":
        return None
    url = item.get("url")
    title = item.get("title")
    if not url or not title:
        return None

    story_id = int(item.get("id"))
    points = float(item.get("score", 0) or 0)
    comments = float(item.get("descendants", 0) or 0)

    # 归一化 popularity_score：简单压缩到 0..1 区间
    pop = (points / 500.0) + (comments / 200.0)
    if pop > 1.0:
        pop = 1.0

    ts = int(item.get("time", 0) or 0)
    published_at = datetime.fromtimestamp(ts, tz=timezone.utc) if ts > 0 else datetime.now(timezone.utc)

    source_meta = {
        "hn_id": story_id,
        "hn_by": item.get("by"),
        "hn_points": points,
        "hn_comments": comments,
        "hn_url": url,
    }

    return Candidate(
        id=f"hn-{story_id}",
        url=url,
        title=title,
        summary=title,  # v0: 先用标题占位，后续由全文抓取 + LLM 生成摘要
        tags="hackernews",
        source="hackernews",
        published_at=published_at,
        popularity_score=pop,
        source_meta=source_meta,
    )


def fetch_candidates_from_hn(source_url: str, *, cfg: _HNConfig | None = None) -> List[Candidate]:
    """从 Hacker News 拉取候选列表。

    - 当前仅根据 list_kind(topstories/newstories) 决定列表类型；
      source_url 暂时只用于未来扩展（例如根据 path 区分 top/new）。
    """

    cfg = cfg or _HNConfig()

    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(f"{_HN_API_BASE}/{cfg.list_kind}.json")
            resp.raise_for_status()
            ids = resp.json()
    except Exception:
        return []

    if not isinstance(ids, list):
        return []

    ids = ids[: cfg.max_items]

    out: List[Candidate] = []

    try:
        with httpx.Client(timeout=20) as client:
            for sid in ids:
                try:
                    r = client.get(f"{_HN_API_BASE}/item/{sid}.json")
                    r.raise_for_status()
                    item = r.json()
                except Exception:
                    continue

                cand = _parse_hn_story(item)
                if cand is not None:
                    out.append(cand)
    except Exception:
        return out

    return out
