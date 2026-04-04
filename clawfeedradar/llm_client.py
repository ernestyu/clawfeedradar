# -*- coding: utf-8 -*-
from __future__ import annotations

"""Small LLM client for bilingual summaries/translation.

OpenAI-compatible chat endpoint, driven by SMALL_LLM_* env vars.
If config is incomplete, the caller should treat summarization as disabled.
"""

import json
import os
import time
from dataclasses import dataclass
from typing import List, Optional

import httpx


@dataclass
class SmallLLMConfig:
    base_url: str
    model: str
    api_key: str
    max_output_chars: int
    sleep_between_ms: int
    source_lang: str  # e.g. "auto", "en"
    target_lang: str  # e.g. "zh"


def load_small_llm_config(
    *, source_lang_override: Optional[str] = None, target_lang_override: Optional[str] = None
) -> Optional[SmallLLMConfig]:
    base_url = os.environ.get("SMALL_LLM_BASE_URL")
    model = os.environ.get("SMALL_LLM_MODEL")
    api_key = os.environ.get("SMALL_LLM_API_KEY")

    if not base_url or not model or not api_key:
        return None

    def _int_env(name: str, default: int) -> int:
        try:
            val = int(os.environ.get(name, ""))
            if val > 0:
                return val
        except Exception:
            pass
        return default

    max_out = _int_env("CLAWFEEDRADAR_LLM_MAX_OUTPUT_CHARS", 12000)
    sleep_ms = _int_env("CLAWFEEDRADAR_LLM_SLEEP_BETWEEN_MS", 500)

    # Language hints: env defaults, CLI overrides
    src_env = os.environ.get("CLAWFEEDRADAR_LLM_SOURCE_LANG", "auto").strip() or "auto"
    tgt_env = os.environ.get("CLAWFEEDRADAR_LLM_TARGET_LANG", "").strip()

    if source_lang_override and source_lang_override.strip():
        src_lang = source_lang_override.strip()
    else:
        src_lang = src_env

    if target_lang_override and target_lang_override.strip():
        tgt_lang = target_lang_override.strip()
    else:
        tgt_lang = tgt_env or "en"

    return SmallLLMConfig(
        base_url=base_url,
        model=model,
        api_key=api_key,
        max_output_chars=max_out,
        sleep_between_ms=sleep_ms,
        source_lang=src_lang,
        target_lang=tgt_lang,
    )


def _chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    if "/v1/" in base:
        return base + "/chat/completions"
    return base + "/v1/chat/completions" if base.startswith("http") else base + "/chat/completions"


def _post_chat(payload: dict, cfg: SmallLLMConfig) -> str:
    url = _chat_url(cfg.base_url)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.api_key}",
        "User-Agent": os.environ.get(
            "CLAWFEEDRADAR_HTTP_USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) Clawfeedradar/0.1",
        ),
    }
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=120)
    except Exception as e:
        raise RuntimeError(f"Small LLM request failed: {e}")

    if resp.status_code >= 400:
        snippet = resp.text[:300]
        raise RuntimeError(
            f"Small LLM HTTPError: {resp.status_code} {resp.reason_phrase} {snippet}"
        )

    body = resp.text
    try:
        data = json.loads(body)
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("invalid content format")
    except Exception as e:
        raise RuntimeError(f"Small LLM response parse failed: {e}; body={body[:300]}")

    # best-effort rate limiting: simple sleep between calls
    if cfg.sleep_between_ms > 0:
        time.sleep(cfg.sleep_between_ms / 1000.0)

    return content


def generate_preview_summary(long_summary: str, cfg: SmallLLMConfig) -> str:
    """Generate a single-language preview summary from a long summary.

    用于 RSS <description> 的短摘要，输入为已经构造好的
    长摘要（约 1200 字 + 最后一段），而不是全文。
    """

    if not long_summary:
        return ""

    text = long_summary
    src = cfg.source_lang or "auto"
    tgt = cfg.target_lang

    sys_prompt = (
        "You are a concise summarization assistant. "
        "Given an article summary, produce a short summary ONLY in the "
        "target language (no other languages). "
        "Keep it around 400-600 characters. "
        f"Source language hint: {src}. Target language: {tgt}."
    )

    user_prompt = (
        "Summarize the following long summary. "
        "Write ONLY in the target language, in at most a few short paragraphs.\n\n" + text
    )

    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        # Let the server decide max_tokens; input is already chunked by context budget.
    }

    return _post_chat(payload, cfg)


def generate_bilingual_body(fulltext: str, cfg: SmallLLMConfig) -> str:
    """Generate paragraph-level bilingual body using chunked LLM calls.

    - Split fulltext into paragraphs by blank lines.
    - Group paragraphs by approximate character budget per call.
    - For each group, call the LLM with a JSON payload containing
      paragraph indexes and texts.
    - Support partial results: any successfully translated paragraphs
      are recorded; remaining ones are retried up to 3 attempts.

    The result is a markdown body with alternating original and
    translated paragraphs.
    """

    if not fulltext:
        return ""

    text = fulltext.replace("\r\n", "\n").replace("\r", "\n")
    raw_paragraphs = [p.strip() for p in text.split("\n\n")]
    paragraphs = [p for p in raw_paragraphs if p]
    if not paragraphs:
        return ""

    # Optional: limit each displayed bilingual segment to a screen-sized chunk.
    # This keeps each original+translation pair within a readable size.
    try:
        max_seg_chars = int(os.environ.get("CLAWFEEDRADAR_LLM_MAX_PARAGRAPH_CHARS", "0") or "0")
    except Exception:
        max_seg_chars = 0
    if max_seg_chars <= 0:
        max_seg_chars = None

    def _split_paragraph_by_screen(para: str):
        """Split a long paragraph into screen-sized segments using sentence boundaries.

        If max_seg_chars is None, the paragraph is returned as-is.
        """
        if max_seg_chars is None or len(para) <= max_seg_chars:
            return [para]
        # naive sentence split by common terminators
        import re as _re
        sentences = []
        start = 0
        for m in _re.finditer(r"[^。！？!?；;]+[。！？!?；;]?", para):
            seg = m.group(0)
            if seg:
                sentences.append(seg)
        if not sentences:
            return [para]
        segments = []
        cur = ""
        for s in sentences:
            if not cur:
                cur = s
            elif len(cur) + len(s) <= max_seg_chars:
                cur += s
            else:
                segments.append(cur)
                cur = s
        if cur:
            segments.append(cur)
        return segments

    # Build screen-sized segments from paragraphs
    screen_segments = []
    for p in paragraphs:
        for seg in _split_paragraph_by_screen(p):
            screen_segments.append(seg)
    if not screen_segments:
        return ""

    indexed = list(enumerate(screen_segments))

    def _group_by_chars(items, max_chars: int):
        chunks = []
        cur = []
        total = 0
        for idx, para in items:
            plen = len(para)
            if plen > max_chars:
                if cur:
                    chunks.append(cur)
                    cur = []
                    total = 0
                chunks.append([(idx, para)])
                continue
            if cur and total + plen + 2 > max_chars:
                chunks.append(cur)
                cur = []
                total = 0
            cur.append((idx, para))
            total += plen + 2
        if cur:
            chunks.append(cur)
        return chunks

    pending = {idx for idx, _ in indexed}
    translations: dict[int, str] = {}
    max_attempts = 3
    attempt = 0

    src = cfg.source_lang or "auto"
    tgt = cfg.target_lang

    while pending and attempt < max_attempts:
        attempt += 1
        to_process = [(idx, screen_segments[idx]) for idx in sorted(pending)]
        try:
            context_chars = int(os.environ.get("CLAWFEEDRADAR_LLM_CONTEXT_CHARS", "8000") or "8000")
        except Exception:
            context_chars = 8000
        if context_chars <= 0:
            context_chars = 8000
        # Use ~40%% of context budget for input paragraphs, leaving room for output.
        input_budget = int(context_chars * 0.4)
        if input_budget <= 0:
            input_budget = context_chars
        chunks = _group_by_chars(to_process, input_budget)

        for chunk in chunks:
            payload_obj = {
                "source_lang": src,
                "target_lang": tgt,
                "paragraphs": [{"idx": idx, "text": para} for idx, para in chunk],
            }
            sys_prompt = (
                "You are a careful bilingual translator. "
                "You receive a JSON object with an array of paragraphs, each "
                "with an 'idx' and 'text'. For each paragraph, produce a "
                "translation into the target language and return a JSON array "
                "of objects of the form {\"idx\": int, \"tgt\": str}. "
                "Do not include any additional commentary."
            )
            user_content = json.dumps(payload_obj, ensure_ascii=False)
            payload = {
                "model": cfg.model,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_content},
                ],
                # max_tokens omitted; we control input size via CLAWFEEDRADAR_LLM_CONTEXT_CHARS.
            }

            try:
                raw = _post_chat(payload, cfg)
                data = json.loads(raw)
            except Exception:
                continue

            if not isinstance(data, list):
                continue

            for item in data:
                idx = item.get("idx")
                tgt_text = item.get("tgt")
                if isinstance(idx, int) and isinstance(tgt_text, str) and idx in pending:
                    translations[idx] = tgt_text.strip()
                    pending.remove(idx)

    parts: List[str] = []
    for idx, para in enumerate(screen_segments):
        parts.append(para)
        tgt_text = translations.get(idx)
        if tgt_text:
            parts.append(tgt_text)
    return "\n\n".join(parts)


def generate_tags_bulk(summaries: list[str], cfg: SmallLLMConfig) -> list[str]:
    """Generate tags for a batch of long summaries using the small LLM.

    - Input: list of long summaries (each ~1200 chars).
    - Output: list of tag strings (same length; empty string on failure).
    - Robustness: up to 3 attempts per summary with simple batch retries.
    """
    if not summaries:
        return []

    results: list[str] = ["" for _ in summaries]
    pending = {i for i, s in enumerate(summaries) if s}
    max_attempts = 3
    attempt = 0

    src = cfg.source_lang or "auto"
    tgt = cfg.target_lang

    # LLM context budget (approximate, in characters) for tag generation input.
    try:
        context_chars = int(os.environ.get("CLAWFEEDRADAR_LLM_CONTEXT_CHARS", "8000") or "8000")
    except Exception:
        context_chars = 8000
    if context_chars <= 0:
        context_chars = 8000

    # Max tags per item (informational hint in the prompt), default 8.
    try:
        max_tags_per_item = int(os.environ.get("CLAWFEEDRADAR_LLM_TAG_MAX_PER_ITEM", "8") or "8")
    except Exception:
        max_tags_per_item = 8
    if max_tags_per_item <= 0:
        max_tags_per_item = 1

    # Chunk indices by rough character budget on input side.
    def _chunks(idxs, n_unused=None):
        chunks = []
        cur = []
        total = 0
        # Use ~80% of context budget for input summaries (rest for output/prompt).
        input_budget = int(context_chars * 0.8)
        if input_budget <= 0:
            input_budget = context_chars
        for i in idxs:
            s = summaries[i]
            plen = len(s)
            # If single summary exceeds budget, put it alone.
            if plen > input_budget:
                if cur:
                    chunks.append(cur)
                    cur = []
                    total = 0
                chunks.append([i])
                continue
            if cur and total + plen + 2 > input_budget:
                chunks.append(cur)
                cur = []
                total = 0
            cur.append(i)
            total += plen + 2
        if cur:
            chunks.append(cur)
        return chunks
        idxs = list(idxs)
        for i in range(0, len(idxs), n):
            yield idxs[i:i+n]

    while pending and attempt < max_attempts:
        attempt += 1
        current = sorted(pending)
        for batch in _chunks(current):
            payload_obj = {
                "source_lang": src,
                "target_lang": tgt,
                "items": [
                    {"idx": i, "summary": summaries[i]} for i in batch
                ],
            }
            sys_prompt = (
                "You are a tagging assistant. "
                "You receive a JSON object with an 'items' array; each item has "
                "an 'idx' and a 'summary'. For each item, produce a concise tag "
                "string (comma-separated keywords) that best describes the topic "
                f"of the summary. Limit to at most {max_tags_per_item} tags per item. "
                "Return a JSON array of objects of the form "
                "{\"idx\": int, \"tags\": str}. Do not include any extra text."
            )
            user_content = json.dumps(payload_obj, ensure_ascii=False)
            payload = {
                "model": cfg.model,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_content},
                ],
                # max_tokens omitted; we control input size via CLAWFEEDRADAR_LLM_CONTEXT_CHARS.
            }
            try:
                raw = _post_chat(payload, cfg)
                data = json.loads(raw)
            except Exception:
                continue

            if not isinstance(data, list):
                continue

            for item in data:
                idx = item.get("idx")
                tags = item.get("tags")
                if isinstance(idx, int) and isinstance(tags, str) and idx in pending:
                    results[idx] = tags.strip()
                    pending.remove(idx)

    # Any remaining pending summaries fallback to empty tag string.
    return results
