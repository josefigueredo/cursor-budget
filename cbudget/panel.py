"""The data behind the Cursor extension's panel, as one JSON-ready dict: `cbudget panel` prints it.

Detail: the cycle against its cap, today against its limits, spend per model, per day and per chat, the model budgets.
Limits: every general limit with its value, kind, help and whether it was changed, the blocked models, the extras and
the check. The panel only shows this; changes go through `cbudget limits`, which validates, logs and notifies.
"""
from __future__ import annotations

import datetime
import time
from pathlib import Path
from typing import Callable

from . import budget, render, update

TOP_CHATS = 5


def data(home: Path = budget.HOME, now: Callable[[], float] = time.time) -> dict:
    t = now()
    cfg, ledger = budget.config(home), budget.read_ledger(home)
    start = budget.cycle_start(t, cfg["cycle_day"])
    reset = render.next_cycle(start, cfg["cycle_day"])
    total, complete = budget.cycle_total(ledger, home, t, cfg["cycle_day"])
    cap = budget.cycle_cap(cfg, home, t)
    series = budget.daily_by_model(ledger, home, t, cfg["cycle_day"])
    today = budget.day(t)

    models = {}
    for day_models in series.values():
        for model, usd in day_models.items():
            models[model] = models.get(model, 0.0) + usd
    chats = {}
    for e in ledger:
        if budget.day(e["t"]) >= start and e.get("conversation_id"):
            c = chats.setdefault(e["conversation_id"], {"conversation_id": e["conversation_id"], "usd": 0.0, "turns": 0,
                                                        "model": e.get("model") or "?", "last": 0})
            c["usd"], c["turns"] = round(c["usd"] + e["usd"], 4), c["turns"] + 1
            if e["t"] >= c["last"]:
                c["last"], c["model"] = e["t"], e.get("model") or "?"
    granted = budget.extras_granted(home, t, cfg["cycle_day"])
    custom = budget.user_config(home)
    problems = budget.check_limits(cfg, budget.known_models(ledger, home), cap, home=home)

    return {
        "cycle": {"total": total, "cap": cap, "limit": float(cfg["cycle_limit"]), "start": start,
                  "reset": reset.isoformat(), "days_left": (reset - datetime.date.fromtimestamp(t)).days,
                  "complete": complete, "warn": float(min(cfg["cycle_warn"]))},
        "today": {"usd": round(sum(series.get(today, {}).values()), 4), "warn": float(cfg["warn_day"]),
                  "block": float(cfg["block_day"])},
        "models": [{"model": m, "usd": round(u, 4)} for m, u in sorted(models.items(), key=lambda kv: -kv[1])],
        "days": [{"date": d, "usd": round(sum(series[d].values()), 4)} for d in sorted(series)],
        "chats": sorted(chats.values(), key=lambda c: -c["usd"])[:TOP_CHATS],
        "budgets": [{"pattern": p, "cap": float(c), "spent": budget.model_spend(ledger, home, t, cfg["cycle_day"], p)}
                    for p, c in cfg["model_budgets"].items()],
        "limits": [{"key": k, "value": cfg[k], "kind": kind, "help": help_text, "custom": k in custom}
                   for k, (kind, help_text) in budget.SCALARS.items()],
        "blocked_models": list(cfg["blocked_models"]),
        "extras": [{"name": budget.extra_name(i), "usd": float(usd), "granted": i < len(granted)}
                   for i, usd in enumerate(cfg["cycle_extras"])],
        "problems": [{"level": lvl, "message": msg} for lvl, msg in problems],
        "version": (update.installed(Path(__file__).resolve().parent.parent) or "")[:7],
    }
