# -*- coding: utf-8 -*-
from __future__ import annotations

"""Main radar run pipeline.

v0: 从 sources 文件拉取候选 → 兴趣打分 → 选出前 N 条 → 写 XML + JSON。
"""

import json
import logging
import os
import sqlite3
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


logger = logging.getLogger("clawfeedradar")



def _build_long_summary(fulltext: str, approx_chars: int = 1200) -> str:
    """Build a long summary from fulltext similar to clawsqlite logic.

    Heuristic:
      - Treat paragraphs separated by blank lines as units.
      - Take leading paragraphs until we reach roughly `approx_chars`
        characters, always stopping at a paragraph boundary.
      - Append the last non-empty paragraph if it is not already included.
    """
    if not fulltext:
        return ""

    # Normalize newlines
    text = fulltext.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""

    head_parts: list[str] = []
    total = 0
    for p in paragraphs:
        head_parts.append(p)
        total += len(p) + 2  # account for blank line separators
        if total >= approx_chars:
            break

    last = paragraphs[-1]
    if last not in head_parts:
        head_parts.append("")  # blank line separator
        head_parts.append(last)

    return "\n\n".join(head_parts)
def _normalize_url(raw: str) -> str:
    """Normalize article URL for deduplication.

    - Strip fragment.
    - Strip trailing slash on path.
    - Drop common tracking query params (utm_*, fbclid, gclid).
    """
    from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

    try:
        parsed = urlparse(raw)
    except Exception:
        return ""

    # Drop fragment
    fragment = ""
    path = parsed.path or ""
    if path != "/":
        # Remove trailing slash (but keep root '/')
        if path.endswith("/"):
            path = path.rstrip("/")

    # Filter query params
    q = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        kl = k.lower()
        if kl.startswith("utm_") or kl in {"fbclid", "gclid"}:
            continue
        q.append((k, v))
    query = urlencode(q)

    normalized = urlunparse((parsed.scheme, parsed.netloc, path, "", query, fragment))
    return normalized


def _load_seen_urls_state(path: Path) -> dict[str, datetime]:
    """Load seen-URLs state, returning url -> datetime (UTC).

    Entries older than 7 days are pruned.
    """
    from datetime import timedelta

    seen: dict[str, datetime] = {}
    if not path.is_file():
        return seen
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return seen
    urls = raw.get("urls") or {}
    now = datetime.now(timezone.utc)
    window = timedelta(days=7)
    for url, ts in urls.items():
        if not isinstance(url, str) or not isinstance(ts, str):
            continue
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if now - dt <= window:
            seen[url] = dt
    return seen


def _save_seen_urls_state(path: Path, seen: dict[str, datetime]) -> None:
    """Persist seen-URLs state to disk."""
    data = {"urls": {url: dt.isoformat() for url, dt in seen.items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _get_interest_clusters_last_built_at(db_path: str):
    """Return last built time for interest clusters from interest_meta table.

    Requires clawsqlite to write a key='interest_clusters_last_built_at'
    ISO-8601 timestamp into interest_meta. Returns a timezone-aware
    datetime or None if unavailable.
    """
    try:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT value FROM interest_meta WHERE key='interest_clusters_last_built_at'"
            ).fetchone()
            if not row:
                return None
            value = row[0]
            if not value:
                return None
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        finally:
            conn.close()
    except Exception:
        return None


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




def _run_pipeline_for_candidates(
    *,
    root: str | None,
    candidates: list[Candidate],
    output_xml: str,
    score_threshold: float,
    max_items: int,
    json_stdout: bool,
    source_lang: str | None,
    target_lang: str | None,
) -> int:
    """Core radar pipeline given an explicit candidate list.

    Shared by both `run_radar` (sources file based) and
    `schedule_from_sources_json` (sources.json based).
    """
    # 确保 root 传递给 config
    if root:
        os.environ["CLAWSQLITE_ROOT"] = root

    cfg = load_config()

    logger.info("[pipeline] using KB at %s", cfg.kb.db_path)

    # warn if clusters are stale (requires clawsqlite to populate interest_meta)
    last_built = _get_interest_clusters_last_built_at(cfg.kb.db_path)
    if last_built is not None:
        age_days = (datetime.now(timezone.utc) - last_built).days
        if age_days >= 7:
            logger.warning(
                "[pipeline] interest_clusters last built %d days ago - consider rerunning 'clawsqlite knowledge build-interest-clusters'",
                age_days,
            )
    else:
        logger.warning(
            "[pipeline] interest_clusters last built time unknown - consider running 'clawsqlite knowledge build-interest-clusters'"
        )

    # 1) load clusters
    clusters = load_clusters(cfg.kb.db_path, cfg.embedding.vec_dim)
    logger.info("[pipeline] loaded %d clusters", len(clusters))
    if not clusters:
        raise RuntimeError("No interest_clusters found; run 'clawsqlite knowledge build-interest-clusters' first")

    if not candidates:
        raise RuntimeError("No candidates provided to radar pipeline")

    # Load global seen-URL state (7-day window)
    state_path = Path.cwd() / "state" / "seen_urls.json"
    seen_urls = _load_seen_urls_state(state_path)
    now = datetime.now(timezone.utc)

    # Filter out candidates whose URLs were seen within the last 7 days
    filtered: list[Candidate] = []
    normalized_urls: list[str] = []
    for c in candidates:
        norm = _normalize_url(c.url)
        if not norm:
            continue
        ts = seen_urls.get(norm)
        if ts is not None and (now - ts).total_seconds() <= 7 * 24 * 3600:
            continue
        filtered.append(c)
        normalized_urls.append(norm)

    if not filtered:
        logger.info("[pipeline] all candidates skipped as already seen in last 7 days")
        return 0

    logger.info("[pipeline] embedding %d candidates", len(filtered))

    # 2) embed and score - fetch fulltext once per URL, then build long summaries
    fulltexts: dict[str, str] = {}
    for c in filtered:
        url = c.url
        if not url or url in fulltexts:
            continue
        fulltexts[url] = fetch_fulltext(url) or ""

    texts: list[str] = []
    for c in filtered:
        fulltext = fulltexts.get(c.url, "")
        long_summary = _build_long_summary(fulltext)
        if not long_summary:
            # Fallback: use title + summary if fulltext is unavailable
            long_summary = f"{c.title}\n\n{c.summary}"
        texts.append(long_summary)

    embs = embed_texts(texts, cfg.embedding)

    score_params = load_score_params_from_env()
    logger.info("[pipeline] scoring %d candidates", len(filtered))
    scored = score_candidates(filtered, embs, clusters, params=score_params)

    # 3) select with basic diversity + exploration
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

    logger.info(
        "[pipeline] selected %d items (score_threshold=%.3f, max_items=%d)",
        len(selected),
        float(score_threshold),
        int(max_items if max_items > 0 else 12),
    )

    # 4) optional: fetch fulltext + LLM summaries (serial, best-effort)
    llm_cfg = load_small_llm_config(source_lang_override=source_lang, target_lang_override=target_lang)
    enriched: list[dict] = []
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

    # 5) write JSON sidecar
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

    # 6) write minimal RSS XML
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

    items_xml: list[str] = []
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
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<rss version=\"2.0\">"
        "<channel>"
        "<title>clawfeedradar</title>"
        "<link>https://example.com/</link>"
        "<description>Personal feed radar powered by clawsqlite.</description>"
        f"<lastBuildDate>{now}</lastBuildDate>"
        + "".join(items_xml) + "</channel></rss>"
    )


    _ensure_parent_dir(str(output_xml_path))
    output_xml_path.write_text(rss, encoding="utf-8")

    # Update seen-URL state for processed candidates
    for norm in normalized_urls:
        seen_urls[norm] = now
    _save_seen_urls_state(state_path, seen_urls)

    return 0


def run_radar(
    *,
    root: str | None,
    url: str,
    output_xml: str,
    score_threshold: float,
    max_items: int,
    json_stdout: bool,
    source_lang: str | None,
    target_lang: str | None,
) -> int:
    """Run radar for a single source URL.

    This is intended for manual runs / debugging: you pass one RSS/HN/etc.
    URL and the pipeline fetches candidates from that source only.
    """
    if not url:
        raise RuntimeError("url is required")

    stype = detect_source_type(url)
    if stype == "unknown":
        raise RuntimeError(f"unknown source type for URL: {url}")

    candidates = fetch_candidates_from_source(stype, url)

    return _run_pipeline_for_candidates(
        root=root,
        candidates=candidates,
        output_xml=output_xml,
        score_threshold=score_threshold,
        max_items=max_items,
        json_stdout=json_stdout,
        source_lang=source_lang,
        target_lang=target_lang,
    )


def schedule_from_sources_json(
    *,
    root: str | None,
    sources_json_path: str,
    output_dir: str,
) -> int:
    """Scan sources.json and run per-source radar when due.

    sources.json 格式示例：

    [
      {
        "label": "hn-frontpage",
        "url": "https://hnrss.org/frontpage",
        "interval_hours": 8,
        "max_entries": 25,
        "source_lang": "en",
        "target_lang": "zh",
        "last_success_at": null,
        "last_error": null
      },
      ...
    ]

    - 配置字段由人维护；
    - last_success_at / last_error 由本函数在每次抓取后更新。
    """

    if root:
        os.environ["CLAWSQLITE_ROOT"] = root

    logger.info("[schedule] scanning sources.json from %s", sources_json_path)

    path = Path(sources_json_path)
    if not path.is_file():
        raise RuntimeError(f"sources.json not found at {sources_json_path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Failed to parse sources.json: {e}") from e

    if not isinstance(data, list):
        raise RuntimeError("sources.json must be a JSON array")

    now = datetime.now(timezone.utc)
    changed = False

    for entry in data:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label") or ""
        url = entry.get("url") or ""
        interval_hours = int(entry.get("interval_hours") or 0)
        max_entries = int(entry.get("max_entries") or 0) or 25
        source_lang = entry.get("source_lang")
        target_lang = entry.get("target_lang")

        if not label or not url or interval_hours <= 0:
            logger.warning("[schedule] skip entry with invalid config: %s", entry)
            continue

        last_success_at_raw = entry.get("last_success_at")
        last_success_at = None
        if isinstance(last_success_at_raw, str):
            try:
                last_success_at = datetime.fromisoformat(last_success_at_raw)
                if last_success_at.tzinfo is None:
                    last_success_at = last_success_at.replace(tzinfo=timezone.utc)
            except Exception:
                last_success_at = None

        due = False
        if last_success_at is None:
            due = True
        else:
            delta = now - last_success_at
            if delta.total_seconds() >= interval_hours * 3600:
                due = True

        if not due:
            logger.info("[schedule] skip %s (not due yet)", label)
            continue

        # 构造 per-source 输出路径：output_dir/{label}.xml
        out_dir = Path(output_dir)
        out_xml = out_dir / f"{label}.xml"

        try:
            stype = detect_source_type(url)
            if stype == "unknown":
                logger.warning("[schedule] unknown source type for %s", url)
                continue
            candidates = fetch_candidates_from_source(stype, url)
            logger.info("[schedule] fetched %d candidates for %s", len(candidates), label)

            _run_pipeline_for_candidates(
                root=root,
                candidates=candidates,
                output_xml=str(out_xml),
                score_threshold=0.0,
                max_items=max_entries,
                json_stdout=False,
                source_lang=source_lang,
                target_lang=target_lang,
            )
            entry["last_success_at"] = now.isoformat()
            entry["last_error"] = None
            changed = True
        except Exception as e:  # noqa: BLE001
            logger.error("[schedule] error while processing %s: %s", label, e)
            entry["last_error"] = str(e)
            changed = True

    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0
