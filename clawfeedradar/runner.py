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
from .embed_client import embed_text, embed_texts
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
    """Persist seen-URLs state to disk.

    Be defensive: if any value in `seen` is not a datetime, try to parse
    it, otherwise skip it.
    """
    urls: dict[str, str] = {}
    for url, dt in seen.items():
        if not isinstance(url, str):
            continue
        if isinstance(dt, datetime):
            urls[url] = dt.isoformat()
        else:
            try:
                parsed = datetime.fromisoformat(str(dt))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                urls[url] = parsed.isoformat()
            except Exception:
                continue
    data = {"urls": urls}
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



def _run_pipeline_for_candidates(
    *,
    root: str | None,
    candidates: list[Candidate],
    output_xml: str,
    feed_title: str | None,
    score_threshold: float,
    max_items: int,
    json_stdout: bool,
    max_source_items: int = 0,
    score_params: ScoreParams | None = None,
    source_lang: str | None,
    target_lang: str | None,
    enable_preview: bool = True,
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
        logger.info("[pipeline] no candidates fetched from source; exiting")
        return 0

    # Load global seen-URL state (7-day window)
    state_path = Path.cwd() / "state" / "seen_urls.json"
    seen_urls = _load_seen_urls_state(state_path)
    logger.info("[pipeline] loaded %d seen URLs (last 7 days window)", len(seen_urls))
    now_dt = datetime.now(timezone.utc)

    # Filter out candidates whose URLs were seen within the last 7 days
    filtered: list[Candidate] = []
    normalized_urls: list[str] = []
    for c in candidates:
        norm = _normalize_url(c.url)
        if not norm:
            continue
        ts = seen_urls.get(norm)
        if ts is not None and (now_dt - ts).total_seconds() <= 7 * 24 * 3600:
            continue
        filtered.append(c)
        normalized_urls.append(norm)

    if not filtered:
        logger.info("[pipeline] all candidates skipped as already seen in last 7 days")
        return 0

        logger.info("[pipeline] embedding %d candidates", len(filtered))

    # 2) embed and score - fetch fulltext once per URL (with per-host serial / cross-host parallel),
    # then build long summaries for embedding.
    fulltexts: dict[str, str] = {}
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from urllib.parse import urlparse
    import threading

    max_workers = int(os.environ.get("CLAWFEEDRADAR_SCRAPE_WORKERS", "4") or "4")
    if max_workers <= 0:
        max_workers = 4

    host_locks: dict[str, threading.Lock] = {}

    def _get_host_lock(host: str) -> threading.Lock:
        lock = host_locks.get(host)
        if lock is None:
            lock = threading.Lock()
            host_locks[host] = lock
        return lock

    def _fetch_for_url(url: str) -> tuple[str, str]:
        host = urlparse(url).netloc
        lock = _get_host_lock(host)
        with lock:
            # Respect existing fetch_fulltext semantics (3 attempts + 3-10s backoff inside).
            text = fetch_fulltext(url) or ""
            return url, text

    urls_to_fetch: list[str] = []
    for c in filtered:
        url = c.url
        if not url or url in fulltexts:
            continue
        urls_to_fetch.append(url)

    if urls_to_fetch:
        # 统计本轮抓取涉及多少个 host，用于审计并发行为（同一 host 内仍然串行）。
        from collections import Counter as _Counter
        host_counter = _Counter(urlparse(u).netloc for u in urls_to_fetch)
        distinct_hosts = len(host_counter)
        logger.info(
            "[pipeline] fulltext fetch: urls=%d, hosts=%d, max_workers=%d",
            len(urls_to_fetch),
            distinct_hosts,
            max_workers,
        )
        if distinct_hosts == 1:
            only_host = next(iter(host_counter.keys()))
            logger.info(
                "[pipeline] single host %r detected; requests to this host are serialized via host-level locks",
                only_host,
            )
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_fetch_for_url, u): u for u in urls_to_fetch}
            for fut in as_completed(futures):
                url, text = fut.result()
                fulltexts[url] = text

    # Build long summaries for each candidate
    long_summaries: dict[str, str] = {}
    summaries_list: list[str] = []
    for c in filtered:
        fulltext = fulltexts.get(c.url, "")
        long_summary = _build_long_summary(fulltext)
        if not long_summary:
            # Fallback: use title + summary if fulltext is unavailable
            long_summary = f"{c.title}\n\n{c.summary}"
        long_summaries[c.url] = long_summary
        summaries_list.append(long_summary)

    # Generate tags for long summaries via small LLM (batch, best-effort)
    logger.info("[llm-tag] generating tags for %d summaries (batch, max_attempts=3)", len(summaries_list))
    llm_cfg_tags = load_small_llm_config()
    if llm_cfg_tags is not None:
        try:
            from .llm_client import generate_tags_bulk
        except Exception:
            llm_cfg_tags = None
    tags_list: list[str] = ["" for _ in summaries_list]
    if llm_cfg_tags is not None:
        try:
            tags_list = generate_tags_bulk(summaries_list, llm_cfg_tags)
            ok = sum(1 for t in tags_list if t)
            logger.info("[llm-tag] generated tags for %d/%d summaries", ok, len(summaries_list))
        except Exception as e:
            logger.warning("[llm-tag] bulk tag generation failed: %s", e)
            tags_list = ["" for _ in summaries_list]
    else:
        logger.info("[llm-tag] small LLM config missing; skip tag generation")

    # Embed summaries and tags (serial embedding client inside embed_texts)
    logger.info("[pipeline] embedding %d summaries (serial)", len(summaries_list))
    summary_embs = embed_texts(summaries_list, cfg.embedding)
    tag_embs: list[list[float]] = []
    logger.info("[pipeline] embedding %d tag texts (serial)", len(tags_list))
    for tags in tags_list:
        if not tags:
            tag_embs.append([0.0] * cfg.embedding.vec_dim)
        else:
            tag_embs.append(embed_text(tags, cfg.embedding))

    # Mix summary/tag embeddings into interest vectors using CLAWSQLITE_INTEREST_TAG_WEIGHT
    try:
        w_tag = float(os.environ.get("CLAWSQLITE_INTEREST_TAG_WEIGHT", "0.75") or "0.75")
    except Exception:
        w_tag = 0.75
    if w_tag < 0.0:
        w_tag = 0.0
    if w_tag > 1.0:
        w_tag = 1.0
    w_sum = 1.0 - w_tag

    interest_embs: list[list[float]] = []
    for s_emb, t_emb in zip(summary_embs, tag_embs):
        if not s_emb and not t_emb:
            interest_embs.append([0.0] * cfg.embedding.vec_dim)
            continue
        # If one vector is effectively zero-length, use the other as-is.
        if all(v == 0.0 for v in s_emb) and any(v != 0.0 for v in t_emb):
            interest_embs.append(t_emb)
            continue
        if all(v == 0.0 for v in t_emb) and any(v != 0.0 for v in s_emb):
            interest_embs.append(s_emb)
            continue
        vec = [0.0] * cfg.embedding.vec_dim
        if w_sum > 0.0:
            for i in range(len(vec)):
                vec[i] += w_sum * s_emb[i]
        if w_tag > 0.0:
            for i in range(len(vec)):
                vec[i] += w_tag * t_emb[i]
        interest_embs.append(vec)

    # Score candidates using interest embeddings and cluster weights
    logger.info("[pipeline] scoring %d candidates", len(filtered))
    from .scoring import score_candidates
    scored = score_candidates(filtered, interest_embs, clusters, params=score_params)

    # 3) select top-N by final_score with a simple score_threshold filter
    total_scored = len(scored)
    if max_items <= 0:
        max_items = 12

    passed = [s for s in scored if s.interest_score >= score_threshold]
    selected = passed[:max_items]

    logger.info(
        "[pipeline] scored=%d, passed_threshold=%d, max_items=%d, selected=%d (score_threshold=%.3f)",
        total_scored,
        len(passed),
        int(max_items),
        len(selected),
        float(score_threshold),
    )

    # 4) optional: LLM summaries (serial, best-effort)
    llm_cfg = load_small_llm_config(source_lang_override=source_lang, target_lang_override=target_lang)
    # If source_lang and target_lang are identical, skip bilingual translation only.
    skip_bilingual = False
    if llm_cfg is not None:
        try:
            src_lang = (llm_cfg.source_lang or "").strip()
            tgt_lang = (llm_cfg.target_lang or "").strip()
            if src_lang and tgt_lang and src_lang.lower() == tgt_lang.lower():
                skip_bilingual = True
                logger.info(
                    "[llm] source_lang == target_lang (%s); skipping bilingual translation calls", src_lang
                )
        except Exception:
            skip_bilingual = False

    enriched: list[dict] = []
    for item in selected:
        c = item.candidate
        # Reuse fulltext fetched earlier in this pipeline when embedding,
        # fall back to a fresh fetch only if missing.
        fulltext = fulltexts.get(c.url, "")
        if not fulltext:
            fulltext = fetch_fulltext(c.url) or ""
        summary_preview = ""
        body_bilingual = ""
        if fulltext and llm_cfg is not None:
            long_summary = long_summaries.get(c.url, "")
            # 先生成预览摘要（除非显式关闭）
            if enable_preview:
                try:
                    src_for_preview = long_summary or fulltext
                    summary_preview = generate_preview_summary(src_for_preview, llm_cfg)
                except Exception as e:
                    logger.warning(
                        "[llm] preview summary failed for URL %s: %s", c.url, e
                    )
                    summary_preview = ""
            # 再生成中英对照正文（除非 source/target 语言相同而跳过）
            if not skip_bilingual:
                try:
                    body_bilingual = generate_bilingual_body(fulltext, llm_cfg)
                except Exception as e:
                    logger.warning(
                        "[llm] bilingual body failed for URL %s: %s", c.url, e
                    )
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

    # 5) write JSON sidecar (same basename as XML, .json extension)
    output_xml_path = Path(output_xml)
    output_dir = output_xml_path.parent
    output_json_path = output_xml_path.with_suffix(".json")

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
    last_build = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

    channel_title = escape(feed_title or "clawfeedradar")

    items_xml: list[str] = []
    for row in enriched:
        c = row["candidate"]
        item = row["item"]
        title = escape(c.title or "")
        link = escape(c.url or "")
        # Combine preview summary and bilingual body for RSS description when available
        preview = row["summary_preview"] or c.summary or ""
        body = row["body_bilingual"] or ""
        if preview and body:
            desc_raw = preview + "\n\n" + body
        elif body:
            desc_raw = body
        else:
            desc_raw = preview
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
        f"<title>{channel_title}</title>"
        "<link>https://example.com/</link>"
        "<description>Personal feed radar powered by clawsqlite.</description>"
        f"<lastBuildDate>{last_build}</lastBuildDate>"
        + "".join(items_xml) + "</channel></rss>"
    )


    _ensure_parent_dir(str(output_xml_path))
    output_xml_path.write_text(rss, encoding="utf-8")

    logger.info("[pipeline] wrote %d items to %s (json=%s); seen_urls now=%d",
                len(enriched), output_xml_path, output_json_path, len(seen_urls))

    # Update seen-URL state for processed candidates (7-day window)
    for norm in normalized_urls:
        seen_urls[norm] = now_dt
    _save_seen_urls_state(state_path, seen_urls)

    return 0


def run_radar(
    *,
    root: str | None,
    url: str,
    output_xml: str,
    feed_title: str | None,
    score_threshold: float,
    max_items: int,
    json_stdout: bool,
    max_source_items: int = 0,
    score_params: ScoreParams | None = None,
    source_lang: str | None,
    target_lang: str | None,
    enable_preview: bool = True,
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

    # max_source_items controls how many feed entries we pull before scoring (0 means adapter default).
    if max_source_items and max_source_items > 0:
        candidates = fetch_candidates_from_source(stype, url, max_items=max_source_items)
    else:
        candidates = fetch_candidates_from_source(stype, url)

    return _run_pipeline_for_candidates(
        root=root,
        candidates=candidates,
        output_xml=output_xml,
        feed_title=feed_title,
        score_threshold=score_threshold,
        max_items=max_items,
        json_stdout=json_stdout,
        max_source_items=max_source_items,
        score_params=score_params,
        source_lang=source_lang,
        target_lang=target_lang,
        enable_preview=enable_preview,
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
        max_source_items = int(entry.get("max_source_items") or 0)
        feed_title = entry.get("feed_title")
        source_lang = entry.get("source_lang")
        target_lang = entry.get("target_lang")
        w_rec = entry.get("w_recency")
        w_pop = entry.get("w_popularity")

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
            if max_source_items and max_source_items > 0:
                candidates = fetch_candidates_from_source(stype, url, max_items=max_source_items)
            else:
                candidates = fetch_candidates_from_source(stype, url)
            logger.info("[schedule] fetched %d candidates for %s", len(candidates), label)

            # score_threshold: per-source config only (no env fallback).
            try:
                score_threshold = float(entry.get("score_threshold") or 0.0)
            except Exception:
                score_threshold = 0.0

            from .scoring import load_score_params_from_env
            base_params = load_score_params_from_env()
            if w_rec is not None:
                try:
                    base_params.w_recency = float(w_rec)
                except Exception:
                    pass
            if w_pop is not None:
                try:
                    base_params.w_popularity = float(w_pop)
                except Exception:
                    pass

            _run_pipeline_for_candidates(
                root=root,
                candidates=candidates,
                output_xml=str(out_xml),
                feed_title=feed_title,
                score_threshold=score_threshold,
                max_items=max_entries,
                json_stdout=False,
                max_source_items=max_source_items,
                score_params=base_params,
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
