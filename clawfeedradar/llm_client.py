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
    target_langs: List[str]


def load_small_llm_config_from_env() -> Optional[SmallLLMConfig]:
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

    langs_raw = os.environ.get("CLAWFEEDRADAR_LLM_TARGET_LANGS", "en,zh")
    target_langs = [x.strip() for x in langs_raw.split(",") if x.strip()]
    if not target_langs:
        target_langs = ["en", "zh"]

    return SmallLLMConfig(
        base_url=base_url,
        model=model,
        api_key=api_key,
        max_input_chars=max_in,
        max_output_chars=max_out,
        sleep_between_ms=sleep_ms,
        target_langs=target_langs,
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


def generate_bilingual_summary(fulltext: str, cfg: SmallLLMConfig) -> str:
    """Call the small LLM to produce a bilingual (EN/ZH) summary.

    v0: return a single markdown-ish block, e.g.:

        [EN]
        ...

        [ZH]
        ...

    The caller can embed this directly into RSS description or JSON.
    """

    if not fulltext:
        return ""

    text = fulltext[: cfg.max_input_chars]

    url = _chat_url(cfg.base_url)

    langs = ",".join(cfg.target_langs)
    sys_prompt = (
        "You are a concise bilingual summarization assistant. "
        "Given an article, produce a short summary in the requested languages. "
        "Write clear markdown with headings for each language. "
        f"Target languages (in order): {langs}."
    )

    user_prompt = (
        "Summarize the following article in the target languages. "
        "Focus on key ideas, not minor details. "
        "Use at most a few short paragraphs per language.\n\n" + text
    )

    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        # Let the server enforce output length; we just pass a soft hint.
        "max_tokens": cfg.max_output_chars // 3 if cfg.max_output_chars > 0 else None,
    }

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
