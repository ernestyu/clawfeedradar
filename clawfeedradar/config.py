# -*- coding: utf-8 -*-
from __future__ import annotations

"""配置加载与路径解析。

v0 只关心：
- CLAWSQLITE_ROOT / CLAWSQLITE_DB
- 嵌入服务配置（EMBEDDING_* / CLAWSQLITE_VEC_DIM）
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class EmbeddingConfig:
    base_url: str
    model: str
    api_key: str
    vec_dim: int


@dataclass
class KBConfig:
    root: str
    db_path: str


@dataclass
class AppConfig:
    kb: KBConfig
    embedding: EmbeddingConfig


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v is not None and v != "" else default


def load_config() -> AppConfig:
    """Load basic config from environment.

    v0 简化：
    - root: CLAWSQLITE_ROOT or ./knowledge_data
    - db: CLAWSQLITE_DB or <root>/knowledge.sqlite3
    - embedding: EMBEDDING_* + CLAWSQLITE_VEC_DIM
    """

    root = _env("CLAWSQLITE_ROOT", os.path.abspath("knowledge_data"))
    db = _env("CLAWSQLITE_DB", os.path.join(root, "knowledge.sqlite3"))

    base_url = _env("EMBEDDING_BASE_URL") or ""
    model = _env("EMBEDDING_MODEL") or ""
    api_key = _env("EMBEDDING_API_KEY") or ""
    vec_dim_str = _env("CLAWSQLITE_VEC_DIM", "0") or "0"
    try:
        vec_dim = int(vec_dim_str)
    except Exception:
        vec_dim = 0

    if not base_url or not model or not api_key or vec_dim <= 0:
        raise RuntimeError(
            "Embedding config invalid: EMBEDDING_BASE_URL/EMBEDDING_MODEL/EMBEDDING_API_KEY "
            "and positive CLAWSQLITE_VEC_DIM are required"
        )

    kb_cfg = KBConfig(root=root, db_path=db)
    emb_cfg = EmbeddingConfig(base_url=base_url, model=model, api_key=api_key, vec_dim=vec_dim)
    return AppConfig(kb=kb_cfg, embedding=emb_cfg)
