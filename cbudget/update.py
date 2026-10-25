"""Whether a newer cursor-budget is on GitHub, and one notification per new version.

install.sh writes the installed commit to app/VERSION. At most once a day a `stop` hook claims the check (update.json)
and starts `cbudget update-check` detached; it asks GitHub to compare that commit with main through `gh`, since the repo
is internal. Only "main has newer commits" notifies, and only once per new commit. No commit recorded, no `gh`, no
network: it says nothing.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from . import budget

SLUG = "josefigueredo/cursor-budget"
DAY = 86400
# Cursor runs hooks with its own PATH, so gh is also looked for where Homebrew puts it.
GH_PLACES = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", str(Path.home() / ".homebrew" / "bin" / "gh"))
UPDATE_COMMAND = (f"gh api repos/{SLUG}/contents/bootstrap.sh -H \"Accept: application/vnd.github.raw\" | bash")


def installed(app: Path) -> Optional[str]:
    try:
        sha = (app / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return sha or None


def state(home: Path) -> dict:
    data = budget.load_json(home / "update.json", {})
    return data if isinstance(data, dict) else {}


def claim(home: Path, t: float) -> bool:
    """Take today's check: True at most once a day. The stamp is written before the check starts, so turns that end
    at the same time do not each start one."""
    data = state(home)
    if t - float(data.get("checked") or 0) < DAY:
        return False
    budget.write_json_atomic(home / "update.json", {**data, "checked": round(t)})
    return True


def parse_compare(answer: dict) -> Optional[tuple]:
    """GitHub's compare/<installed>...main as (state of the install, newest commit on main). GitHub says "ahead" when
    main is ahead of the install, which means the install is behind."""
    status = answer.get("status")
    if status not in ("ahead", "behind", "identical", "diverged"):
        return None
    commits = answer.get("commits") or []
    newest = commits[-1].get("sha") if commits else None
    return {"ahead": "behind", "behind": "ahead"}.get(status, status), newest


def gh_path() -> Optional[str]:
    return shutil.which("gh") or next((p for p in GH_PLACES if Path(p).exists()), None)


def compare_with_main(sha: str) -> Optional[tuple]:
    gh = gh_path()
    if not gh:
        return None
    try:
        done = subprocess.run([gh, "api", f"repos/{SLUG}/compare/{sha}...main"], capture_output=True, timeout=20)
        return parse_compare(json.loads(done.stdout)) if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def check(home: Path, app: Path, now: Callable[[], float] = time.time,
          compare: Callable[[str], Optional[tuple]] = compare_with_main,
          notify: Callable[[str], None] = lambda msg: None) -> Optional[str]:
    """Compare the installed commit with main and notify once per newer commit. Returns the install's state."""
    sha = installed(app)
    if not sha:
        return None
    answer = compare(sha)
    if not answer:
        return None
    status, newest = answer
    data = {**state(home), "checked": round(now()), "status": status, "latest": newest}
    if status == "behind" and newest and data.get("notified") != newest:
        notify("Hay una versión nueva de cursor-budget. Para actualizar, decile al agente «actualizá cursor-budget» "
               "o corré `cbudget update` en una terminal.")
        data["notified"] = newest
    budget.write_json_atomic(home / "update.json", data)
    return status
