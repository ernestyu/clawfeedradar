# -*- coding: utf-8 -*-
from __future__ import annotations

"""Small LLM client for bilingual summaries/translation.

v0: OpenAI-compatible chat endpoint, driven by SMALL_LLM_* env vars.
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
    max_input_chars: int
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

    max_in = _int_env("CLAWFEEDRADAR_LLM_MAX_INPUT_CHARS", 6000)
    max_out = _int_env("CLAWFEEDRADAR_LLM_MAX_OUTPUT_CHARS", 6000)
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
        max_input_chars=max_in,
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


def generate_preview_summary(fulltext: str, cfg: SmallLLMConfig) -> str:
    """Generate a *single-language* preview summary in cfg.target_lang.

    Intended for RSS <description> and quick scanning.
    """

    if not fulltext:
        return ""

    text = fulltext[: cfg.max_input_chars]

    src = cfg.source_lang or "auto"
    tgt = cfg.target_lang

    sys_prompt = (
        "You are a concise summarization assistant. "
        "Given an article, produce a short summary ONLY in the target language. "
        "Do not include any other language. "
        f"Source language hint: {src}. Target language: {tgt}."
    )

    user_prompt = (
        "Summarize the following article. "
        "Write ONLY in the target language, in at most a few short paragraphs.\n\n" + text
    )

    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": cfg.max_output_chars // 3 if cfg.max_output_chars > 0 else None,
    }

    return _post_chat(payload, cfg)


def generate_bilingual_body(fulltext: str, cfg: SmallLLMConfig) -> str:
    """Generate paragraph-level bilingual body.

    For each paragraph of the original article, output:
    - first the original paragraph
    - then its translation into cfg.target_lang

    The result is a markdown body suitable for detailed reading.
    """

    if not fulltext:
        return ""

    text = fulltext[: cfg.max_input_chars]
    src = cfg.source_lang or "auto"
    tgt = cfg.target_lang

    sys_prompt = (
        "You are a careful bilingual translator. "
        "Given an article, split it into paragraphs. For each paragraph, "
        "first output the original text, then on the next paragraph output "
        "its translation into the target language. "
        "Keep the original ordering. Use plain markdown paragraphs, no tables. "
        f"Source language hint: {src}. Target language: {tgt}."
    )

    user_prompt = "Produce paragraph-level bilingual text for the following article:\n\n" + text

    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": cfg.max_output_chars // 2 if cfg.max_output_chars > 0 else None,
    }

    return _post_chat(payload, cfg)
