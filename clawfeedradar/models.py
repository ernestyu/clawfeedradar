# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List


@dataclass
class Candidate:
    """统一候选文章模型（来自任意源）。"""

    id: str
    url: str
    title: str
    summary: str
    tags: str
    source: str
    published_at: datetime
    popularity_score: float
    source_meta: Dict[str, Any]


@dataclass
class ClusterInfo:
    """兴趣簇信息，对应 interest_clusters 表的一行。"""

    id: int
    label: str
    size: int
    centroid: List[float]


@dataclass
class InterestMatch:
    """候选与兴趣簇匹配结果。"""

    best_cluster_id: int
    sim_best: float
    sim_second: float


@dataclass
class ScoredItem:
    """打分后的候选，用于排序和输出。"""

    candidate: Candidate
    interest_score: float
    final_score: float
    match: InterestMatch
