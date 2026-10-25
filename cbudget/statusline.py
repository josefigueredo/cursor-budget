"""The spend in cursor-agent's footer: its `statusLine` runs `cbudget statusline`, which prints one line.

cursor-agent replaces its own footer with this line, so the line also carries what the footer showed first: the model
and how full the context is. It must never be empty (an empty line blanks the footer), so any error prints the model.
The key lives in ~/.cursor/cli-config.json, next to the session login: it is edited with a strict read, every other
key kept, an atomic write and the same permissions, and a status line someone else set is never replaced.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from . import budget

CLI_CONFIG = Path.home() / ".cursor" / "cli-config.json"
INTERVAL_MS = 5000  # the line reads the ledger; every 5 s is enough for a spend that changes once per turn


def line(payload: dict, home: Path = budget.HOME, now: Callable[[], float] = time.time) -> str:
    model = ((payload or {}).get("model") or {}).get("display_name") or "cursor-budget"
    try:
        cfg, t = budget.config(home), now()
        entries = budget.read_ledger(home)
        _, today = budget.totals(entries, None, budget.day(t))
        cycle, _ = budget.cycle_total(entries, home, t, cfg["cycle_day"])
        parts = [model]
        used = ((payload or {}).get("context_window") or {}).get("used_percentage")
        if used is not None:
            parts.append(f"contexto {float(used):.0f}%")
        parts.append(f"hoy ${today:.2f} de ${float(cfg['block_day']):.0f}")
        parts.append(f"ciclo ${cycle:.2f} de ${budget.cycle_cap(cfg, home, t):.0f}")
        return " · ".join(parts)
    except Exception:  # noqa: BLE001 - the footer must always show something
        return model


def _ours(entry) -> bool:
    return isinstance(entry, dict) and str(entry.get("command", "")).split()[-1:] == ["statusline"] \
        and "cbudget.py" in str(entry.get("command", ""))


def install(command: str, path: Path = CLI_CONFIG) -> bool:
    """Point cursor-agent's status line at `command`. True when the file changed; ValueError when it cannot."""
    if not path.exists():
        raise ValueError(f"no encontré {path}: abrí cursor-agent una vez y volvé a correr esto")
    data = budget.read_json_strict(path)
    current = data.get("statusLine")
    if current is not None and not _ours(current):
        raise ValueError(f"cursor-agent ya tiene otra statusLine en {path}; no la reemplacé")
    wanted = {"type": "command", "command": command, "updateIntervalMs": INTERVAL_MS}
    if current == wanted:
        return False
    data["statusLine"] = wanted
    budget.write_json_atomic(path, data)
    return True


def remove(path: Path = CLI_CONFIG) -> bool:
    """Take out cbudget's status line, and only that. True when the file changed."""
    if not path.exists():
        return False
    data = budget.read_json_strict(path)
    if not _ours(data.get("statusLine")):
        return False
    del data["statusLine"]
    budget.write_json_atomic(path, data)
    return True
