# -*- coding: utf-8 -*-
from __future__ import annotations

"""打分与排序（v1）。

核心设计：
- interest_score = 对所有兴趣簇的 (cluster_weight * similarity) 求和，
  其中 cluster_weight = cluster_size / total_articles。
- final_score 在 interest_score 基础上只做轻度源特化偏置（如 HN 热度）。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List
import os

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


def _float_env(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name, "")
        if not raw:
            return default
        val = float(raw)
        return val
    except Exception:
        return default


def load_score_params_from_env() -> ScoreParams:
    """Load ScoreParams from environment variables (with sane defaults).

    - CLAWFEEDRADAR_W_SIM_BEST
    - CLAWFEEDRADAR_W_SIM_SECOND
    - CLAWFEEDRADAR_W_RECENCY
    - CLAWFEEDRADAR_W_POPULARITY
    - CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS
    """

    return ScoreParams(
        w_sim_best=_float_env("CLAWFEEDRADAR_W_SIM_BEST", 0.6),
        w_sim_second=_float_env("CLAWFEEDRADAR_W_SIM_SECOND", 0.2),
        w_recency=_float_env("CLAWFEEDRADAR_W_RECENCY", 0.1),
        w_popularity=_float_env("CLAWFEEDRADAR_W_POPULARITY", 0.1),
        recency_half_life=_float_env("CLAWFEEDRADAR_RECENCY_HALF_LIFE_DAYS", 3.0) * 24 * 3600.0,
    )


def _recency_weight(published_at: datetime, now: datetime, half_life: float) -> float:
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    dt = max(0.0, (now - published_at).total_seconds())
    # 指数衰减：越新权重越高
    return 0.5 ** (dt / half_life) if half_life > 0 else 0.0



# 源特化通道 --------------------------------------------

def score_generic_extra(cand: Candidate, base: float) -> float:
    return 0.0


def score_hn_extra(cand: Candidate, base: float) -> float:
    """基于 HN points/comments 的附加分。"""

    meta = cand.source_meta or {}
    points = float(meta.get("hn_points", 0) or 0)
    comments = float(meta.get("hn_comments", 0) or 0)
    # 归一化到大致 0..1 区间
    s_points = min(1.0, points / 500.0)
    s_comments = min(1.0, comments / 100.0)
    return 0.5 * s_points + 0.5 * s_comments


def score_arxiv_extra(cand: Candidate, base: float) -> float:
    """基于 arxiv 的简单附加分：偏向近期论文。

    v0: 只看 recency（全局已有 recency 权重，这里只给很轻微的补充）。
    """

    # 这里先简单返回 0，后续如有需要可根据 published_at 再加一点增益。
    return 0.0


SOURCE_SCORERS: Dict[str, Any] = {
    "hackernews": score_hn_extra,
    "arxiv": score_arxiv_extra,
}


def _lambda_source(name: str, default_val: float) -> float:
    return _float_env(name, default_val)


LAMBDA_SOURCE: Dict[str, float] = {
    "hackernews": _lambda_source("CLAWFEEDRADAR_LAMBDA_HN", 0.2),
    "arxiv": _lambda_source("CLAWFEEDRADAR_LAMBDA_ARXIV", 0.1),
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

    v1: interest_score = sum_k (cluster_weight_k * sim_k),
        其中 cluster_weight_k = cluster.size / sum_j cluster_j.size。
    时间和源特化通道仅作轻度偏置，不再参与兴趣主轴。
    """

    if not cands or not embs or not clusters:
        return []

    # 预先计算簇权重：size / total_size
    total_size = sum(max(1, int(c.size or 0)) for c in clusters)
    if total_size <= 0:
        total_size = 1
    cluster_weights = {c.id: max(1, int(c.size or 0)) / total_size for c in clusters}

    params = params or load_score_params_from_env()
    now = datetime.now(timezone.utc)
    out: List[ScoredItem] = []

    # 为每个候选计算 interest_score
    for cand, emb in zip(cands, embs):
        # 与所有簇计算相似度，并按簇权重加权求和
        from .sqlite_interest import _cosine_sim

        total = 0.0
        best_cluster_id = -1
        best_sim = 0.0
        second_sim = 0.0
        for c in clusters:
            sim = _cosine_sim(emb, c.centroid)
            if sim < 0.0:
                sim = 0.0
            w = cluster_weights.get(c.id, 0.0)
            total += w * sim
            # 跟踪 best/second 仅用于日志和可解释性
            if sim > best_sim:
                second_sim = best_sim
                best_sim = sim
                best_cluster_id = c.id
            elif sim > second_sim:
                second_sim = sim

        interest = float(total)
        match = InterestMatch(best_cluster_id=best_cluster_id, sim_best=best_sim, sim_second=second_sim)

        # 时间与源特化作为轻度偏置
        rec = _recency_weight(cand.published_at, now, params.recency_half_life)
        pop = max(0.0, min(1.0, cand.popularity_score))
        # 这里给 recency/popularity 较小权重，确保兴趣主轴主导排序
        interest_bias = (params.w_recency * rec + params.w_popularity * pop)
        biased_interest = interest + interest_bias

        final = compute_final_score(cand, biased_interest)
        out.append(ScoredItem(candidate=cand, interest_score=interest, final_score=final, match=match))

    out.sort(key=lambda x: x.final_score, reverse=True)
    return out
