# -*- coding: utf-8 -*-
from __future__ import annotations

"""Self-check utilities for clawfeedradar.

`python -m clawfeedradar.cli doctor` 会运行一组检查，输出一份
JSON 报告，帮助你快速判断：

- 当前环境变量配置是否完整；
- clawsqlite 知识库和兴趣簇是否可用；
- 抓全文命令是否配置；
- 小 LLM / git 发布之类的可选组件是否就绪。

该命令不会修改任何数据，只做只读检查。
"""

import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import load_config, _env
from .sqlite_interest import load_clusters


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str
    next: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


def _check_embedding() -> CheckResult:
    cfg = load_config()
    if cfg is None:
        msg = (
            "Embedding config invalid: EMBEDDING_BASE_URL/EMBEDDING_MODEL/EMBEDDING_API_KEY "
            "and positive CLAWSQLITE_VEC_DIM are required."
        )
        return CheckResult(
            name="embedding",
            ok=False,
            message=msg,
            next=(
                "Set EMBEDDING_BASE_URL/EMBEDDING_MODEL/EMBEDDING_API_KEY and "
                "a positive CLAWSQLITE_VEC_DIM in the project .env or environment, "
                "then rerun clawfeedradar doctor."
            ),
        )

    return CheckResult(
        name="embedding",
        ok=True,
        message=(
            f"Embedding service configured: model={cfg.embedding.model!r}, "
            f"vec_dim={cfg.embedding.vec_dim}, base_url={cfg.embedding.base_url!r}"
        ),
        details={
            "model": cfg.embedding.model,
            "vec_dim": cfg.embedding.vec_dim,
            "base_url": cfg.embedding.base_url,
        },
    )


def _check_kb_and_db() -> CheckResult:
    cfg = load_config()
    if cfg is None:
        return CheckResult(
            name="knowledge_base",
            ok=False,
            message="Knowledge base config unavailable because embedding config is invalid.",
            next="Fix embedding config first; KB root/DB path are read from the same env.",
        )

    root = cfg.kb.root
    db_path = cfg.kb.db_path
    root_exists = Path(root).is_dir()
    db_exists = Path(db_path).is_file()

    if not root_exists and not db_exists:
        return CheckResult(
            name="knowledge_base",
            ok=False,
            message=(
                f"Knowledge root {root!r} and DB {db_path!r} do not exist."
            ),
            next=(
                "Create a clawsqlite knowledge_data at CLAWSQLITE_ROOT and run "
                "'clawsqlite knowledge ingest' + 'build-interest-clusters' before running clawfeedradar."
            ),
        )

    if root_exists and not db_exists:
        return CheckResult(
            name="knowledge_base",
            ok=False,
            message=(
                f"Knowledge root exists at {root!r}, but DB file {db_path!r} is missing."
            ),
            next=(
                "Point CLAWSQLITE_DB to an existing clawsqlite DB, or run a first ingest "
                "via 'clawsqlite knowledge ingest' to initialize the DB."
            ),
            details={"root": root, "db_path": db_path},
        )

    if not root_exists and db_exists:
        return CheckResult(
            name="knowledge_base",
            ok=True,
            message=(
                f"DB file exists at {db_path!r}, but CLAWSQLITE_ROOT {root!r} directory is missing."
            ),
            next=(
                "Consider setting CLAWSQLITE_ROOT to the directory that contains your articles/ "
                "and matches the DB configuration, or recreate the expected directory tree."
            ),
            details={"root": root, "db_path": db_path},
        )

    return CheckResult(
        name="knowledge_base",
        ok=True,
        message=f"Knowledge DB found at {db_path!r} under root {root!r}",
        details={"root": root, "db_path": db_path},
    )


def _check_interest_clusters() -> CheckResult:
    cfg = load_config()
    if cfg is None:
        return CheckResult(
            name="interest_clusters",
            ok=False,
            message="Cannot check interest_clusters because embedding/KB config is invalid.",
            next=(
                "Fix EMBEDDING_* / CLAWSQLITE_VEC_DIM / CLAWSQLITE_ROOT / CLAWSQLITE_DB first, "
                "then rerun 'clawsqlite knowledge build-interest-clusters' and 'clawfeedradar doctor'."
            ),
        )

    db_path = cfg.kb.db_path
    vec_dim = cfg.embedding.vec_dim

    # Try loading clusters to verify schema + centroid dims.
    try:
        clusters = load_clusters(db_path, vec_dim)
    except Exception as e:  # pragma: no cover - defensive
        return CheckResult(
            name="interest_clusters",
            ok=False,
            message=f"Failed to load interest_clusters from DB at {db_path!r}: {e}",
            next=(
                "Ensure clawsqlite>=1.0.0 is installed and that you have run "
                "'clawsqlite knowledge build-interest-clusters' successfully."
            ),
        )

    if not clusters:
        return CheckResult(
            name="interest_clusters",
            ok=False,
            message="No interest_clusters found in DB.",
            next=(
                "Run 'clawsqlite knowledge build-interest-clusters' on your knowledge DB "
                "before using clawfeedradar."
            ),
        )

    return CheckResult(
        name="interest_clusters",
        ok=True,
        message=f"Loaded {len(clusters)} interest_clusters from DB.",
        details={"num_clusters": len(clusters)},
    )


def _check_scraper() -> CheckResult:
    cmd = _env("CLAWFEEDRADAR_SCRAPE_CMD")
    if not cmd:
        return CheckResult(
            name="scraper",
            ok=False,
            message="CLAWFEEDRADAR_SCRAPE_CMD is not set.",
            next=(
                "Set CLAWFEEDRADAR_SCRAPE_CMD to a command that accepts a URL and writes markdown "
                "to stdout (e.g. a clawfetch wrapper), then rerun doctor."
            ),
        )

    return CheckResult(
        name="scraper",
        ok=True,
        message=f"Scraper command configured: {cmd!r}",
        details={"scrape_cmd": cmd},
    )


def _check_output_dir() -> CheckResult:
    out_dir = _env("CLAWFEEDRADAR_OUTPUT_DIR") or str(Path.cwd() / "feeds")
    p = Path(out_dir)
    if p.exists() and not p.is_dir():
        return CheckResult(
            name="output_dir",
            ok=False,
            message=f"CLAWFEEDRADAR_OUTPUT_DIR points to a non-directory path: {out_dir!r}",
            next="Point CLAWFEEDRADAR_OUTPUT_DIR to a directory path (it will be created if missing).",
        )

    return CheckResult(
        name="output_dir",
        ok=True,
        message=f"Feeds will be written under {out_dir!r}",
        details={"output_dir": out_dir},
    )


def _check_small_llm() -> CheckResult:
    base = _env("SMALL_LLM_BASE_URL")
    model = _env("SMALL_LLM_MODEL")
    key = _env("SMALL_LLM_API_KEY")
    if not base and not model and not key:
        return CheckResult(
            name="small_llm",
            ok=False,
            message="Small LLM not configured (SMALL_LLM_BASE_URL/MODEL/API_KEY all empty).",
            next=(
                "If you want preview summaries and bilingual bodies, set SMALL_LLM_BASE_URL/" 
                "SMALL_LLM_MODEL/SMALL_LLM_API_KEY. Otherwise, you can ignore this warning."
            ),
        )

    if not (base and model and key):
        return CheckResult(
            name="small_llm",
            ok=False,
            message="Small LLM config is partially set.",
            next="Set all of SMALL_LLM_BASE_URL, SMALL_LLM_MODEL, and SMALL_LLM_API_KEY or clear them all.",
            details={"base_url": base, "model": model, "has_key": bool(key)},
        )

    return CheckResult(
        name="small_llm",
        ok=True,
        message=f"Small LLM configured: model={model!r}, base_url={base!r}",
        details={"base_url": base, "model": model},
    )


def _check_git_publish() -> CheckResult:
    repo = _env("CLAWFEEDRADAR_PUBLISH_GIT_REPO")
    if not repo:
        return CheckResult(
            name="git_publish",
            ok=False,
            message="Git publish is not configured (CLAWFEEDRADAR_PUBLISH_GIT_REPO empty).",
            next=(
                "If you want clawfeedradar to push feeds to GitHub/Gitee Pages, set "
                "CLAWFEEDRADAR_PUBLISH_GIT_REPO/BRANCH/PATH as described in the README."
            ),
        )

    branch = _env("CLAWFEEDRADAR_PUBLISH_GIT_BRANCH", "gh-pages")
    path = _env("CLAWFEEDRADAR_PUBLISH_GIT_PATH", "feeds")
    return CheckResult(
        name="git_publish",
        ok=True,
        message=f"Git publish configured for repo={repo!r}, branch={branch!r}, path={path!r}",
        details={"repo": repo, "branch": branch, "path": path},
    )


def run_doctor() -> int:
    checks: List[CheckResult] = []

    checks.append(_check_embedding())
    checks.append(_check_kb_and_db())
    checks.append(_check_interest_clusters())
    checks.append(_check_scraper())
    checks.append(_check_output_dir())
    checks.append(_check_small_llm())
    checks.append(_check_git_publish())

    any_error = any(not c.ok for c in checks if c.name in {"embedding", "knowledge_base", "interest_clusters"})

    report = {
        "ok": not any_error,
        "checks": [asdict(c) for c in checks],
    }

    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    print()  # newline
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_doctor())
