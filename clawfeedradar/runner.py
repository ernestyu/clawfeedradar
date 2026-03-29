# -*- coding: utf-8 -*-
from __future__ import annotations

"""Main radar run pipeline.

v0: 从 sources 文件拉取候选 → 兴趣打分 → 选出前 N 条 → 写 XML + JSON。
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from xml.sax.saxutils import escape

from .config import load_config
from .embed_client import embed_text
from .llm_client import load_small_llm_config, generate_bilingual_summary
from .models import Candidate
from .scoring import ScoreParams, score_candidates
from .scrape import fetch_fulltext
from .sources import detect_source_type, fetch_candidates_from_source
from .sqlite_interest import load_clusters


def _load_sources_file(path: str) -> List[str]:
    out: List[str] = []
    p = Path(path)
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _ensure_parent_dir(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def run_radar(
    *,
    root: str | None,
    sources_file: str,
    output_xml: str,
    score_threshold: float,
    max_items: int,
    json_stdout: bool,
) -> int:
    # 确保 root 传递给 config
    if root:
        os.environ["CLAWSQLITE_ROOT"] = root

    cfg = load_config()

    # 1) load clusters
    clusters = load_clusters(cfg.kb.db_path, cfg.embedding.vec_dim)
    if not clusters:
        raise RuntimeError("No interest_clusters found; run 'clawsqlite knowledge build-interest-clusters' first")

    # 2) load sources and fetch candidates
    source_urls = _load_sources_file(sources_file)
    candidates: List[Candidate] = []
    for url in source_urls:
        stype = detect_source_type(url)
        if stype == "unknown":
            continue
        cands = fetch_candidates_from_source(stype, url)
        candidates.extend(cands)

    if not candidates:
        raise RuntimeError("No candidates fetched from sources; check sources file and network")

    # 3) embed and score
    texts = [f"{c.title}\n\n{c.summary}" for c in candidates]
    embs = [embed_text(t, cfg.embedding) for t in texts]

    scored = score_candidates(candidates, embs, clusters, params=ScoreParams())

    # 4) filter & truncate
    selected = [s for s in scored if s.interest_score >= score_threshold]
    if max_items > 0:
        selected = selected[: max_items]

    # 5) optional: fetch fulltext + bilingual summaries (serial, best-effort)
    llm_cfg = load_small_llm_config_from_env()
    enriched: List[dict] = []
    for item in selected:
        c = item.candidate
        fulltext = fetch_fulltext(c.url) or ""
        summary_llm = ""
        if fulltext and llm_cfg is not None:
            try:
                summary_llm = generate_bilingual_summary(fulltext, llm_cfg)
            except Exception:
                summary_llm = ""

        enriched.append(
            {
                "candidate": c,
                "item": item,
                "fulltext": fulltext,
                "summary_llm": summary_llm,
            }
        )

    # 6) write JSON sidecar
    output_xml_path = Path(output_xml)
    output_dir = output_xml_path.parent
    output_json_path = output_dir / "radar.json"

    payload = []
    for row in enriched:
        c = row["candidate"]
        item = row["item"]
        payload.append(
            {
                "id": c.id,
                "url": c.url,
                "title": c.title,
                "summary": c.summary,
                "source": c.source,
                "published_at": c.published_at.isoformat(),
                "popularity_score": c.popularity_score,
                "interest_score": item.interest_score,
                "final_score": item.final_score,
                "best_cluster_id": item.match.best_cluster_id,
                "sim_best": item.match.sim_best,
                "sim_second": item.match.sim_second,
                "fulltext": row["fulltext"],
                "summary_bilingual": row["summary_llm"],
            }
        )

    _ensure_parent_dir(str(output_json_path))
    output_dir.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if json_stdout:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    # 7) write minimal RSS XML
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

    items_xml: List[str] = []
    for item in selected:
        c = item.candidate
        title = escape(c.title or "")
        link = escape(c.url or "")
        desc = escape(c.summary or "")
        pub_date = c.published_at.strftime("%a, %d %b %Y %H:%M:%S %z")
        items_xml.append(
            f"<item>"
            f"<title>{title}</title>"
            f"<link>{link}</link>"
            f"<description>{desc}</description>"
            f"<pubDate>{pub_date}</pubDate>"
            f"</item>"
        )

    rss = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"\
        "<rss version=\"2.0\">"\
        "<channel>"\
        "<title>clawfeedradar</title>"\
        "<link>https://example.com/</link>"\
        "<description>Personal feed radar powered by clawsqlite.</description>"\
        f"<lastBuildDate>{now}</lastBuildDate>"\
        + "".join(items_xml) + "</channel></rss>"
    )

    _ensure_parent_dir(str(output_xml_path))
    output_xml_path.write_text(rss, encoding="utf-8")

    return 0
