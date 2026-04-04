# -*- coding: utf-8 -*-
from __future__ import annotations

"""Publishing helpers for clawfeedradar.

v1: generic git-based publishing to support GitHub Pages / Gitee Pages.

If the following env vars are set:

- CLAWFEEDRADAR_PUBLISH_GIT_REPO   (e.g. git@github.com:user/repo.git or git@gitee.com:user/repo.git)
- CLAWFEEDRADAR_PUBLISH_GIT_BRANCH (e.g. gh-pages)
- CLAWFEEDRADAR_PUBLISH_GIT_PATH   (e.g. feeds)

Then `publish_via_git` will:

1. Ensure a local clone exists under `./.publish/<slug>/`.
2. Copy the generated XML/JSON into that clone under the given path.
3. Commit and push changes.

This is intentionally simple and best-effort: errors are reported as
[error] messages with suggested next actions, and the caller can decide
whether to treat a publish failure as fatal.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Tuple, List

from .runner import logger  # reuse the main logger


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _slug_from_repo_url(url: str) -> str:
    # git@github.com:user/repo.git -> user-repo
    # https://github.com/user/repo.git -> user-repo
    if not url:
        return ""
    base = url
    if "@" in base:
        base = base.split(":", 1)[-1]
    if base.startswith("http://") or base.startswith("https://"):
        base = base.rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[:-4]
    return base.replace("/", "-") or "repo"


def _run(cmd: List[str], cwd: Path) -> Tuple[int, str]:
    try:
        out = subprocess.check_output(cmd, cwd=str(cwd), stderr=subprocess.STDOUT, text=True)
        return 0, out
    except subprocess.CalledProcessError as e:
        return e.returncode, e.output


def publish_via_git(output_xml: str) -> int:
    """Publish the given XML (and its JSON sidecar) to a git repo if configured.

    Returns 0 on success, non-zero on failure. When not configured, returns 0.
    """

    repo_url = _env("CLAWFEEDRADAR_PUBLISH_GIT_REPO")
    branch = _env("CLAWFEEDRADAR_PUBLISH_GIT_BRANCH") or "gh-pages"
    rel_path = _env("CLAWFEEDRADAR_PUBLISH_GIT_PATH") or "feeds"

    if not repo_url:
        # Not configured; nothing to do.
        return 0

    try:
        out_xml = Path(output_xml).resolve()
    except Exception as e:
        msg = f"[error] invalid output_xml path {output_xml!r}: {e}"
        print(msg)
        logger.error(msg)
        return 1

    out_json = out_xml.with_suffix(".json")

    base_dir = Path.cwd() / ".publish"
    base_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug_from_repo_url(repo_url)
    clone_dir = base_dir / slug

    # 1) Ensure local clone exists
    if not clone_dir.exists():
        code, out = _run(["git", "clone", repo_url, str(clone_dir)], cwd=base_dir)
        if code != 0:
            msg = (
                f"[error] failed to clone {repo_url!r} into {str(clone_dir)!r}: {out}\n"
                "Ensure the repo exists and SSH/PAT auth is configured (e.g. ssh keys added or git credential helper)."
            )
            print(msg)
            logger.error(msg)
            return 1

    # 2) Checkout target branch (create if needed)
    code, out = _run(["git", "checkout", branch], cwd=clone_dir)
    if code != 0:
        # Try to create the branch
        code2, out2 = _run(["git", "checkout", "-b", branch], cwd=clone_dir)
        if code2 != 0:
            msg = (
                f"[error] failed to checkout/create branch {branch!r} in {str(clone_dir)!r}: {out}\n{out2}\n"
                "Create the branch manually or ensure you have permission to push new branches."
            )
            print(msg)
            logger.error(msg)
            return 1

    # 3) Copy XML/JSON into clone under rel_path
    target_dir = clone_dir / rel_path
    target_dir.mkdir(parents=True, exist_ok=True)

    target_xml = target_dir / out_xml.name
    shutil.copy2(out_xml, target_xml)
    if out_json.is_file():
        target_json = target_dir / out_json.name
        shutil.copy2(out_json, target_json)

    # 4) git add + commit + push
    code, out = _run(["git", "add", str(target_xml.relative_to(clone_dir))], cwd=clone_dir)
    if code != 0:
        msg = f"[error] git add failed in {str(clone_dir)!r}: {out}"
        print(msg)
        logger.error(msg)
        return 1
    if out_json.is_file():
        _run(["git", "add", str((target_dir / out_json.name).relative_to(clone_dir))], cwd=clone_dir)

    code, out = _run(["git", "commit", "-m", "update feeds from clawfeedradar"], cwd=clone_dir)
    if code != 0:
        # Likely no changes; treat as success.
        if "nothing to commit" not in (out or ""):
            logger.info("[publish] git commit produced: %s", out)

    code, out = _run(["git", "push", "origin", branch], cwd=clone_dir)
    if code != 0:
        msg = (
            f"[error] git push failed for {repo_url!r} branch {branch!r}: {out}\n"
            "Check your network and repo permissions; you may need to configure SSH keys or tokens."
        )
        print(msg)
        logger.error(msg)
        return 1

    logger.info(
        "[publish] pushed %s (and JSON sidecar if present) to %s on branch %s under %s",
        out_xml.name,
        repo_url,
        branch,
        rel_path,
    )
    return 0
