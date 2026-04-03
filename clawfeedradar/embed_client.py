# -*- coding: utf-8 -*-
from __future__ import annotations

"""Embedding client for clawfeedradar.

v0: 使用与 clawsqlite 相同的 OpenAI-compatible embeddings API，
仅支持简单的单段文本输入，用于 Candidate 向量化。
"""

import json
import logging
import os
import time
from typing import List

import httpx

from .config import EmbeddingConfig


logger = logging.getLogger("clawfeedradar")


def _embeddings_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/embeddings"):
        return base
    if base.endswith("/v1"):
        return base + "/embeddings"
    if "/v1/" in base:
        return base + "/embeddings"
    return base + "/v1/embeddings" if base.startswith("http") else base + "/embeddings"


def embed_text(text: str, cfg: EmbeddingConfig, *, timeout: int = 60) -> List[float]:
    """调用 embedding 服务，将文本编码为向量。"""

    if not text:
        return [0.0] * cfg.vec_dim

    url = _embeddings_url(cfg.base_url)
    payload = {"model": cfg.model, "input": text}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.api_key}",
        "User-Agent": os.environ.get(
            "CLAWFEEDRADAR_HTTP_USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) Clawfeedradar/0.1",
        ),
    }

    # simple retry loop for transient timeouts
    max_retries = int(os.environ.get("CLAWFEEDRADAR_EMBED_RETRIES", "3") or "3")
    if max_retries < 1:
        max_retries = 1
    backoff = float(os.environ.get("CLAWFEEDRADAR_EMBED_RETRY_BACKOFF_SEC", "1.0") or "1.0")

    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
            break
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as e:
            last_exc = e
            if attempt < max_retries:
                logger.warning(
                    "[embedding] timeout (attempt %d/%d, %d chars), retrying in %.1fs: %s",
                    attempt,
                    max_retries,
                    len(text),
                    backoff,
                    e,
                )
                time.sleep(backoff)
                continue
            logger.warning(
                "[embedding] timeout after %d attempts for %d chars, returning zero vector: %s",
                max_retries,
                len(text),
                e,
            )
            return [0.0] * cfg.vec_dim
        except Exception as e:
            logger.error("[embedding] request failed: %s", e)
            raise RuntimeError(f"Embedding request failed: {e}")

    if resp.status_code >= 400:
        snippet = resp.text[:300]
        logger.warning("[embedding] HTTPError %s %s: %s; degrading to zero vector", resp.status_code, resp.reason_phrase, snippet)
        return [0.0] * cfg.vec_dim

    body = resp.text
    try:
        data = json.loads(body)
        emb = data["data"][0]["embedding"]
        if not isinstance(emb, list):
            raise ValueError("Invalid embedding format")
        vec = [float(x) for x in emb]
        # 简单对齐维度：截断或补零（预期情况下长度应等于 vec_dim）。
        if len(vec) >= cfg.vec_dim:
            return vec[: cfg.vec_dim]
        else:
            return vec + [0.0] * (cfg.vec_dim - len(vec))
    except Exception as e:
        logger.error("[embedding] response parse failed: %s; body=%s", e, body[:300])
        raise RuntimeError(f"Embedding response parse failed: {e}; body={body[:300]}")


def embed_texts(texts: list[str], cfg: EmbeddingConfig, *, timeout: int = 60) -> list[list[float]]:
    """Serial embedding helper.

    We deliberately avoid batching to reduce memory pressure on the
    embedding server and keep behavior predictable.
    """
    if not texts:
        return []
    return [embed_text(t, cfg, timeout=timeout) for t in texts]
