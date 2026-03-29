# -*- coding: utf-8 -*-
from __future__ import annotations

"""从 clawsqlite 知识库读取兴趣簇，并提供相似度计算。"""

import math
import sqlite3
from typing import List

from .models import ClusterInfo, InterestMatch


def load_clusters(db_path: str, vec_dim: int) -> List[ClusterInfo]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, label, size, summary_centroid FROM interest_clusters ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()

    clusters: List[ClusterInfo] = []

    import struct

    for r in rows:
        blob = r["summary_centroid"]
        if blob is None:
            continue
        if len(blob) != 4 * vec_dim:
            # 维度不匹配时跳过该簇
            continue
        vec = list(struct.unpack("<" + "f" * vec_dim, blob))
        clusters.append(
            ClusterInfo(
                id=int(r["id"]),
                label=str(r["label"] or f"cluster-{r['id']}"),
                size=int(r["size"]),
                centroid=vec,
            )
        )
    return clusters


def _cosine_sim(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


def score_against_clusters(emb: List[float], clusters: List[ClusterInfo]) -> InterestMatch:
    """计算候选向量与各兴趣簇中心的相似度，返回最相近簇信息。"""

    if not clusters or not emb:
        return InterestMatch(best_cluster_id=-1, sim_best=0.0, sim_second=0.0)

    best_id = -1
    best_sim = -1.0
    second_sim = -1.0

    for c in clusters:
        sim = _cosine_sim(emb, c.centroid)
        if sim > best_sim:
            second_sim = best_sim
            best_sim = sim
            best_id = c.id
        elif sim > second_sim:
            second_sim = sim

    if best_sim < 0.0:
        best_sim = 0.0
    if second_sim < 0.0:
        second_sim = 0.0

    return InterestMatch(best_cluster_id=best_id, sim_best=best_sim, sim_second=second_sim)
