"""Cost of a Cursor turn, the local ledger, and the warn and block decisions.

Cursor's `stop` payload carries model, input_tokens, output_tokens, cache_read_tokens and cache_write_tokens per turn
(measured 2026-10-24 with cursor-agent 2026.10.22). input_tokens includes the cached ones: a second turn reported
22934 input with 19807 cache_read. So the uncached input is input - cache_read - cache_write.
"""
from __future__ import annotations

import fnmatch
import json
import time
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
HOME = Path.home() / ".cursor" / "budget"

DEFAULTS = {
    "warn_conversation": 2.0,
    "block_conversation": 5.0,
    "warn_day": 8.0,
    "block_day": 15.0,
    "blocked_models": ["claude-opus-*-high", "*-xhigh", "*-max"],
}


def load_json(path: Path, fallback: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def config(home: Path = HOME) -> dict:
    return {**DEFAULTS, **load_json(home / "config.json", {})}


def prices(home: Path = HOME) -> dict:
    """A copy in ~/.cursor/budget/prices.json overrides the shipped table."""
    shipped = load_json(HERE / "prices.json", {})
    return load_json(home / "prices.json", shipped) if (home / "prices.json").exists() else shipped


def rate(model: str, table: dict) -> dict:
    for row in table.get("models", []):
        if model.startswith(row["prefix"]):
            return row
    return table["unknown"]


def cost(event: dict, table: dict) -> float:
    r = rate(event.get("model") or "", table)
    tokens = {k: int(event.get(k) or 0) for k in
              ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")}
    uncached = max(tokens["input_tokens"] - tokens["cache_read_tokens"] - tokens["cache_write_tokens"], 0)
    write_mult = r.get("cache_write_multiplier", table.get("cache_write_multiplier", 1.0))
    usd = (uncached * r["input"] + tokens["cache_write_tokens"] * r["input"] * write_mult
           + tokens["cache_read_tokens"] * r["cache_read"] + tokens["output_tokens"] * r["output"])
    if r.get("surcharge"):
        usd += (tokens["input_tokens"] + tokens["output_tokens"]) * table.get("surcharge_per_mtok", 0.0)
    return round(usd / 1e6, 4)


def day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


def read_ledger(home: Path = HOME) -> list:
    entries = []
    try:
        lines = (home / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return entries
    for line in lines:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def totals(entries: list, conversation: Optional[str], today: str) -> tuple:
    conv = sum(e["usd"] for e in entries if conversation and e.get("conversation_id") == conversation)
    daily = sum(e["usd"] for e in entries if day(e["t"]) == today)
    return round(conv, 4), round(daily, 4)


def crossed(before: float, after: float, limit: float) -> bool:
    return before < limit <= after


def on_stop(payload: dict, home: Path = HOME, now: Callable[[], float] = time.time,
            notify: Callable[[str], None] = lambda msg: None) -> dict:
    """Record the turn and warn once when a conversation or the day crosses its warn or block limit."""
    cfg, table, t = config(home), prices(home), now()
    usd = cost(payload, table)
    entries = read_ledger(home)
    conv_id = payload.get("conversation_id")
    conv_before, day_before = totals(entries, conv_id, day(t))
    entry = {"t": round(t), "conversation_id": conv_id, "model": payload.get("model"), "usd": usd,
             **{k: payload.get(k) for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                                             "cache_write_tokens")}}
    home.mkdir(parents=True, exist_ok=True)
    with (home / "ledger.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    conv_after, day_after = round(conv_before + usd, 4), round(day_before + usd, 4)
    for limit_key, before, after, what in (
            ("block_conversation", conv_before, conv_after, "este chat"),
            ("warn_conversation", conv_before, conv_after, "este chat"),
            ("block_day", day_before, day_after, "hoy"),
            ("warn_day", day_before, day_after, "hoy")):
        if crossed(before, after, cfg[limit_key]):
            tail = " Abrí un chat nuevo." if what == "este chat" else ""
            notify(f"Cursor: {what} ya va ~${after:.2f} (límite {limit_key} ${cfg[limit_key]:.0f}).{tail}")
            break
    return {}


def allowed_until(home: Path = HOME) -> float:
    return float(load_json(home / "allow.json", {}).get("until", 0))


def on_submit(payload: dict, home: Path = HOME, now: Callable[[], float] = time.time,
              notify: Callable[[str], None] = lambda msg: None) -> dict:
    """Block the prompt when the model is on the blocked list or a block limit is spent, unless `allow` is active."""
    cfg, t = config(home), now()
    if allowed_until(home) > t:
        return {}
    model = payload.get("model") or ""
    conv, daily = totals(read_ledger(home), payload.get("conversation_id"), day(t))
    reason = None
    if any(fnmatch.fnmatch(model, pattern) for pattern in cfg["blocked_models"]):
        reason = f"el modelo {model} está bloqueado; elegí otro o corré `cbudget allow`"
    elif conv >= cfg["block_conversation"]:
        reason = f"este chat ya costó ~${conv:.2f}; abrí un chat nuevo"
    elif daily >= cfg["block_day"]:
        reason = f"hoy ya van ~${daily:.2f}; corré `cbudget allow` si de verdad hace falta"
    if not reason:
        return {}
    notify(f"Cursor bloqueado: {reason}")
    return {"continue": False, "user_message": f"cursor-budget: {reason}"}


def allow(minutes: int, home: Path = HOME, now: Callable[[], float] = time.time) -> float:
    until = now() + minutes * 60
    home.mkdir(parents=True, exist_ok=True)
    (home / "allow.json").write_text(json.dumps({"until": until}), encoding="utf-8")
    return until
