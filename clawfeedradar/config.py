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


_PROJECT_ENV_LOADED = False


def load_project_env() -> None:
    """Load .env from project root with higher precedence than system env.

    Precedence order (highest to lowest):
    1. CLI arguments (handled in cli.py and not touched here)
    2. Project .env file at repo root (clawfeedradar/.env)
    3. Existing process environment variables.

    This helper parses simple KEY=VALUE lines; lines starting with '#' or
    without '=' are ignored. Quotes around VALUE are stripped.
    """
    global _PROJECT_ENV_LOADED
    if _PROJECT_ENV_LOADED:
        return
    _PROJECT_ENV_LOADED = True

    try:
        from pathlib import Path

        root_dir = Path(__file__).resolve().parents[1]
        env_path = root_dir / '.env'
        if not env_path.is_file():
            return
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            # strip simple quotes
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            os.environ[key] = value
    except Exception:
        # best-effort; config loading will still rely on existing env
        return


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


def load_config() -> AppConfig | None:
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
        # For Agent-friendly behavior, avoid raising here. Caller should handle a None AppConfig
        # and print a clear message to stdout.
        return None

    kb_cfg = KBConfig(root=root, db_path=db)
    emb_cfg = EmbeddingConfig(base_url=base_url, model=model, api_key=api_key, vec_dim=vec_dim)
    return AppConfig(kb=kb_cfg, embedding=emb_cfg)
