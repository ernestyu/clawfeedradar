# -*- coding: utf-8 -*-
from __future__ import annotations

"""打分与排序（v0）。

当前仅实现：
- 通用兴趣分 Interest Score（基于聚类和 embedding）
- 源特化通道占位（仅 hackernews 示例）
- 简单的主线排序（不含探索/多样性配额，后续补充）
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List

from .models import Candidate, ClusterInfo, InterestMatch, ScoredItem
from .sqlite_interest import score_against_clusters


@dataclass
class ScoreParams:
    # 权重：兴趣主通道
    w_sim_best: float = 0.6
    w_sim_second: float = 0.2
    w_recency: float = 0.1
    w_popularity: float = 0.1

    # recency 衰减时间尺度（秒），例如 3 天
    recency_half_life: float = 3 * 24 * 3600.0


def _recency_weight(published_at: datetime, now: datetime, half_life: float) -> float:
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    dt = max(0.0, (now - published_at).total_seconds())
    # 指数衰减：越新权重越高
    return 0.5 ** (dt / half_life) if half_life > 0 else 0.0


def compute_interest_score(
    cand: Candidate,
    emb: List[float],
    clusters: List[ClusterInfo],
    params: ScoreParams,
    *,
    now: datetime | None = None,
) -> tuple[float, InterestMatch]:
    """通用兴趣分：不依赖源特化字段。"""

    now = now or datetime.now(timezone.utc)
    match = score_against_clusters(emb, clusters)
    sim_best = match.sim_best
    sim_second = match.sim_second
    rec = _recency_weight(cand.published_at, now, params.recency_half_life)
    pop = max(0.0, min(1.0, cand.popularity_score))

    interest = (
        params.w_sim_best * sim_best
        + params.w_sim_second * sim_second
        + params.w_recency * rec
        + params.w_popularity * pop
    )
    return interest, match


# 源特化通道占位 --------------------------------------------

def score_generic_extra(cand: Candidate, base: float) -> float:
    return 0.0


def score_hn_extra(cand: Candidate, base: float) -> float:
    meta = cand.source_meta or {}
    points = float(meta.get("hn_points", 0) or 0)
    comments = float(meta.get("hn_comments", 0) or 0)
    return 0.5 * (points / 500.0) + 0.5 * (comments / 100.0)


SOURCE_SCORERS: Dict[str, Any] = {
    "hackernews": score_hn_extra,
}

LAMBDA_SOURCE: Dict[str, float] = {
    "hackernews": 0.2,
    "default": 0.1,
}


def compute_final_score(cand: Candidate, interest_score: float) -> float:
    extra_fn = SOURCE_SCORERS.get(cand.source, score_generic_extra)
    lam = LAMBDA_SOURCE.get(cand.source, LAMBDA_SOURCE["default"])
    extra = float(extra_fn(cand, interest_score) or 0.0)
    return interest_score + lam * extra


def score_candidates(
    cands: List[Candidate],
    embs: List[List[float]],
    clusters: List[ClusterInfo],
    params: ScoreParams | None = None,
) -> List[ScoredItem]:
    """对一批候选打分并按 final_score 排序（降序）。

    v0：不做簇配额/探索，仅验证 end-to-end 行为。
    """

    params = params or ScoreParams()
    now = datetime.now(timezone.utc)
    out: List[ScoredItem] = []

    for cand, emb in zip(cands, embs):
        interest, match = compute_interest_score(cand, emb, clusters, params, now=now)
        final = compute_final_score(cand, interest)
        out.append(ScoredItem(candidate=cand, interest_score=interest, final_score=final, match=match))

    out.sort(key=lambda x: x.final_score, reverse=True)
    return out
