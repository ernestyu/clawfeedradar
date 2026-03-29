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
from .llm_client import load_small_llm_config, generate_preview_summary, generate_bilingual_body
from .models import Candidate
from .scoring import ScoreParams, score_candidates, load_score_params_from_env
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


def _select_items_with_diversity(
    scored: List["ScoredItem"],
    *,
    score_threshold: float,
    max_items: int,
    per_cluster_cap: int,
    explore_count: int,
) -> List["ScoredItem"]:
    """Select items with basic diversity + exploration.

    - 先按 interest_score 过滤，再按 final_score 排序；
    - exploitation：限制每个簇最多 per_cluster_cap 条；
    - exploration：从剩余候选中挑若干“边界型”条目（sim_second 高且 sim_best 不极端）。
    """

    from .models import ScoredItem  # type: ignore  # avoid circular type issues

    if max_items <= 0:
        return []

    pool: List[ScoredItem] = [s for s in scored if s.interest_score >= score_threshold]
    if not pool:
        return []

    pool.sort(key=lambda x: x.final_score, reverse=True)

    max_main = max_items
    if explore_count > 0 and max_items > explore_count:
        max_main = max_items - explore_count

    # Exploitation: cluster quotas
    per_cluster: dict[int, int] = {}
    main_selected: List[ScoredItem] = []
    for item in pool:
        if len(main_selected) >= max_main:
            break
        cid = item.match.best_cluster_id
        if cid is None:
            cid = -1
        count = per_cluster.get(cid, 0)
        if count >= per_cluster_cap:
            continue
        main_selected.append(item)
        per_cluster[cid] = count + 1

    used_ids = {id(it) for it in main_selected}
    remaining: List[ScoredItem] = [s for s in pool if id(s) not in used_ids]

    # Exploration: prefer items near multiple clusters (sim_second 高且 sim_best 不极端)
    def _explore_score(it: ScoredItem) -> float:
        sb = float(it.match.sim_best)
        ss = float(it.match.sim_second)
        return ss * (1.0 - sb)

    explore_selected: List[ScoredItem] = []
    if explore_count > 0 and remaining:
        remaining_sorted = sorted(remaining, key=_explore_score, reverse=True)
        for it in remaining_sorted:
            if len(explore_selected) >= explore_count:
                break
            if len(main_selected) + len(explore_selected) >= max_items:
                break
            explore_selected.append(it)

    return main_selected + explore_selected


def run_radar(
    *,
    root: str | None,
    sources_file: str,
    output_xml: str,
    score_threshold: float,
    max_items: int,
    json_stdout: bool,
    source_lang: str | None,
    target_lang: str | None,
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

    score_params = load_score_params_from_env()
    scored = score_candidates(candidates, embs, clusters, params=score_params)

    # 4) select with basic diversity + exploration
    # diversity 配额与探索数量可以通过环境变量调节
    def _int_env(name: str, default: int) -> int:
        try:
            raw = os.environ.get(name, "")
            if not raw:
                return default
            v = int(raw)
            if v <= 0:
                return default
            return v
        except Exception:
            return default

    per_cluster_cap = _int_env("CLAWFEEDRADAR_PER_CLUSTER_CAP", 3)
    explore_count = _int_env("CLAWFEEDRADAR_EXPLORE_COUNT", 2)

    selected = _select_items_with_diversity(
        scored,
        score_threshold=score_threshold,
        max_items=max_items if max_items > 0 else 12,
        per_cluster_cap=per_cluster_cap,
        explore_count=explore_count,
    )

    # 5) optional: fetch fulltext + LLM summaries (serial, best-effort)
    llm_cfg = load_small_llm_config(source_lang_override=source_lang, target_lang_override=target_lang)
    enriched: List[dict] = []
    for item in selected:
        c = item.candidate
        fulltext = fetch_fulltext(c.url) or ""
        summary_preview = ""
        body_bilingual = ""
        if fulltext and llm_cfg is not None:
            try:
                summary_preview = generate_preview_summary(fulltext, llm_cfg)
                body_bilingual = generate_bilingual_body(fulltext, llm_cfg)
            except Exception:
                summary_preview = ""
                body_bilingual = ""

        enriched.append(
            {
                "candidate": c,
                "item": item,
                "fulltext": fulltext,
                "summary_preview": summary_preview,
                "body_bilingual": body_bilingual,
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
                "summary_preview": row["summary_preview"],
                "body_bilingual": row["body_bilingual"],
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
    for row in enriched:
        c = row["candidate"]
        item = row["item"]
        title = escape(c.title or "")
        link = escape(c.url or "")
        # Prefer LLM preview summary if available; fall back to original summary
        desc_raw = row["summary_preview"] or c.summary or ""
        desc = escape(desc_raw)
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
