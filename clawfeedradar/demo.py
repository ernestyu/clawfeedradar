# -*- coding: utf-8 -*-
from __future__ import annotations

"""v0 demo：使用假数据跑一遍打分链路。

- 从 ENV 加载 clawsqlite/embedding 配置
- 从 knowledge.sqlite3 读取兴趣簇
- 构造几条假 Candidate
- 调用 embedding + scoring 输出 JSON
"""

import json
from datetime import datetime, timezone, timedelta
from typing import List

from .config import load_config
from .embed_client import embed_text
from .models import Candidate
from .scoring import score_candidates, ScoreParams
from .sqlite_interest import load_clusters


def _fake_candidates(now: datetime) -> List[Candidate]:
    base_time = now
    return [
        Candidate(
            id="demo-1",
            url="https://example.com/sqlite-vector-search",
            title="Efficient vector search with SQLite",
            summary="An article about using sqlite-vec for semantic search.",
            tags="sqlite,vector,search",
            source="demo",
            published_at=base_time - timedelta(hours=2),
            popularity_score=0.8,
            source_meta={},
        ),
        Candidate(
            id="demo-2",
            url="https://example.com/llm-agents",
            title="Building robust LLM agents",
            summary="Practical notes on building robust LLM agents with tool use.",
            tags="llm,agents,tooling",
            source="demo",
            published_at=base_time - timedelta(days=1),
            popularity_score=0.6,
            source_meta={},
        ),
        Candidate(
            id="demo-3",
            url="https://example.com/trading-risk",
            title="Risk management in systematic trading",
            summary="Discussion of risk engines and position sizing.",
            tags="trading,risk,engine",
            source="demo",
            published_at=base_time - timedelta(days=3),
            popularity_score=0.4,
            source_meta={},
        ),
    ]


def run_demo() -> int:
    cfg = load_config()

    # 1) load clusters from clawsqlite
    clusters = load_clusters(cfg.kb.db_path, cfg.embedding.vec_dim)
    if not clusters:
        raise RuntimeError("No interest_clusters found; run 'clawsqlite knowledge build-interest-clusters' first")

    now = datetime.now(timezone.utc)

    # 2) build fake candidates
    cands = _fake_candidates(now)

    # 3) embed candidates (title+summary)
    embs = []
    for c in cands:
        text = f"{c.title}\n\n{c.summary}"
        embs.append(embed_text(text, cfg.embedding))

    # 4) score and print JSON
    scored = score_candidates(cands, embs, clusters, params=ScoreParams())

    payload = []
    for item in scored:
        payload.append(
            {
                "id": item.candidate.id,
                "title": item.candidate.title,
                "final_score": item.final_score,
                "interest_score": item.interest_score,
                "best_cluster_id": item.match.best_cluster_id,
                "sim_best": item.match.sim_best,
                "sim_second": item.match.sim_second,
            }
        )

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0
