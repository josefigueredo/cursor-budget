"""Cost of a Cursor turn, the local ledger, and the warn and block decisions.

Cursor's `stop` payload carries model, input_tokens, output_tokens, cache_read_tokens and cache_write_tokens per turn
(measured 2026-10-24 with cursor-agent 2026.10.22). input_tokens includes the cached ones: a second turn reported
22934 input with 19807 cache_read. So the uncached input is input - cache_read - cache_write.
"""
from __future__ import annotations

import datetime
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
    "cycle_day": 1,
    "cycle_limit": 225.0,
    "cycle_warn": [0.75, 0.9, 1.0],
    "notify_each_turn": True,
    "model_budgets": {},
    "model_warn": 0.8,
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


def cycle_start(t: float, cycle_day: int) -> str:
    """The date the billing cycle containing t started: day `cycle_day` of this month or the previous one."""
    d = datetime.date.fromtimestamp(t)
    if d.day >= cycle_day:
        return d.replace(day=cycle_day).isoformat()
    prev = d.replace(day=1) - datetime.timedelta(days=1)
    return prev.replace(day=min(cycle_day, prev.day)).isoformat()


def cycle_total(entries: list, home: Path, t: float, cycle_day: int) -> tuple:
    """Spend this cycle: what Cursor billed up to the seeded CSV, plus the hook's estimates after it.
    Returns (usd, seeded) where seeded says whether a baseline for this cycle exists."""
    start = cycle_start(t, cycle_day)
    base = load_json(home / "baseline.json", {})
    seeded = base.get("cycle_start") == start
    billed, until = (float(base.get("billed", 0)), float(base.get("until", 0))) if seeded else (0.0, 0.0)
    estimated = sum(e["usd"] for e in entries if e["t"] > until and day(e["t"]) >= start)
    return round(billed + estimated, 4), seeded


def budget_for(model: str, budgets: dict) -> Optional[str]:
    """The first pattern in model_budgets that matches the model, in the order written in config.json."""
    return next((pattern for pattern in budgets if fnmatch.fnmatch(model, pattern)), None)


def model_spend(entries: list, home: Path, t: float, cycle_day: int, pattern: str) -> float:
    """Spend this cycle on the models a pattern matches: billed per the seeded CSV plus estimates after it."""
    start = cycle_start(t, cycle_day)
    base = load_json(home / "baseline.json", {})
    seeded = base.get("cycle_start") == start
    until = float(base.get("until", 0)) if seeded else 0.0
    billed = sum(v for m, v in (base.get("by_model") or {}).items() if fnmatch.fnmatch(m, pattern)) if seeded else 0
    estimated = sum(e["usd"] for e in entries if e["t"] > until and day(e["t"]) >= start
                    and fnmatch.fnmatch(e.get("model") or "", pattern))
    return round(billed + estimated, 4)


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
    cycle_after, seeded = cycle_total(entries + [entry], home, t, cfg["cycle_day"])
    cycle_before, limit = round(cycle_after - usd, 4), cfg["cycle_limit"]
    since = "" if seeded else " desde que se instaló; corré `cbudget seed <csv>`"
    for share in sorted(cfg["cycle_warn"], reverse=True):
        if crossed(cycle_before, cycle_after, limit * share):
            notify(f"Cursor: el ciclo ya va ~${cycle_after:.2f} de ${limit:.0f} ({cycle_after / limit:.0%}){since}.")
            break
    else:
        if cfg["notify_each_turn"]:
            notify(f"turno ~${usd:.2f} · chat ~${conv_after:.2f} · ciclo ~${cycle_after:.2f} de ${limit:.0f}{since}")
    pattern = budget_for(payload.get("model") or "", cfg["model_budgets"])
    if pattern:
        cap = float(cfg["model_budgets"][pattern])
        spent = model_spend(entries + [entry], home, t, cfg["cycle_day"], pattern)
        for share in (1.0, cfg["model_warn"]):
            if crossed(spent - usd, spent, cap * share):
                notify(f"Cursor: {pattern} ya usó ~${spent:.2f} de sus ${cap:.0f} del ciclo ({spent / cap:.0%}).")
                break
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


def block_reason(model: str, conversation: Optional[str], cfg: dict, home: Path, t: float) -> Optional[str]:
    """The first rule that stops this prompt, or None."""
    if any(fnmatch.fnmatch(model, pattern) for pattern in cfg["blocked_models"]):
        return f"el modelo {model} está bloqueado; elegí otro o corré `cbudget allow`"
    entries = read_ledger(home)
    pattern = budget_for(model, cfg["model_budgets"])
    if pattern:
        cap = float(cfg["model_budgets"][pattern])
        spent = model_spend(entries, home, t, cfg["cycle_day"], pattern)
        if spent >= cap:
            return (f"{model} agotó su presupuesto del ciclo ({pattern}: ~${spent:.2f} de ${cap:.0f}); "
                    f"elegí otro o corré `cbudget allow`")
    conv, daily = totals(entries, conversation, day(t))
    if conv >= cfg["block_conversation"]:
        return f"este chat ya costó ~${conv:.2f}; abrí un chat nuevo"
    if daily >= cfg["block_day"]:
        return f"hoy ya van ~${daily:.2f}; corré `cbudget allow` si de verdad hace falta"
    return None


def on_submit(payload: dict, home: Path = HOME, now: Callable[[], float] = time.time,
              notify: Callable[[str], None] = lambda msg: None) -> dict:
    """Block the prompt when the model is on the blocked list or a block limit is spent, unless `allow` is active."""
    cfg, t = config(home), now()
    if allowed_until(home) > t:
        return {}
    model = payload.get("model") or ""
    reason = block_reason(model, payload.get("conversation_id"), cfg, home, t)
    if not reason:
        return {}
    try:  # evidence that a block fired, also where the user_message is not shown
        home.mkdir(parents=True, exist_ok=True)
        with (home / "blocks.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": round(t), "conversation_id": payload.get("conversation_id"), "model": model,
                                 "reason": reason}) + "\n")
    except OSError:
        pass
    notify(f"Cursor bloqueado: {reason}")
    return {"continue": False, "user_message": f"cursor-budget: {reason}"}


def allow(minutes: int, home: Path = HOME, now: Callable[[], float] = time.time) -> float:
    until = now() + minutes * 60
    home.mkdir(parents=True, exist_ok=True)
    (home / "allow.json").write_text(json.dumps({"until": until}), encoding="utf-8")
    return until


def csv_event(row: dict) -> tuple:
    """A row of Cursor's usage export as (label, billed usd, hook-shaped event). Auto-routed rows read
    `grok-4.7-high (Auto Balanced)`: priced by the model before the parenthesis."""
    def num(key: str) -> float:
        try:
            return float(row.get(key) or 0)
        except ValueError:  # "Free"
            return 0.0
    write, read = num("Input (w/ Cache Write)"), num("Cache Read")
    event = {"model": (row.get("Model") or "").split(" (")[0], "cache_write_tokens": write, "cache_read_tokens": read,
             "input_tokens": write + read + num("Input (w/o Cache Write)"), "output_tokens": num("Output Tokens")}
    return row.get("Model") or "", num("Cost"), event


def calibrate(rows, table: dict) -> list:
    """Billed against estimated per model: (model, events, billed, estimated, matched prefix)."""
    acc = {}
    for row in rows:
        label, billed, event = csv_event(row)
        if label == "usage-correction":
            continue
        n, b, e = acc.get(label, (0, 0.0, 0.0))
        acc[label] = (n + 1, b + billed, e + cost(event, table))
    out = [(m, n, round(b, 2), round(e, 2), rate(m.split(" (")[0], table).get("prefix", "UNKNOWN"))
           for m, (n, b, e) in acc.items()]
    return sorted(out, key=lambda r: -r[2])


def seed(rows, home: Path = HOME, now: Callable[[], float] = time.time, cycle_day: Optional[int] = None) -> dict:
    """Store what Cursor billed this cycle according to its usage CSV, as the base for cycle_total."""
    t = now()
    start = cycle_start(t, cycle_day or config(home)["cycle_day"])
    billed, until, by_model, by_day = 0.0, 0.0, {}, {}
    for row in rows:
        stamp = datetime.datetime.fromisoformat((row.get("Date") or "").replace("Z", "+00:00")).timestamp()
        if day(stamp) < start:
            continue
        label, usd, _ = csv_event(row)
        model = "auto" if "(Auto" in label else label  # the hook only ever sees auto-<mode> for these
        billed += usd
        by_model[model] = round(by_model.get(model, 0.0) + usd, 4)
        cell = by_day.setdefault(day(stamp), {})
        cell[model] = round(cell.get(model, 0.0) + usd, 4)
        until = max(until, stamp)
    base = {"cycle_start": start, "billed": round(billed, 2), "until": until, "by_model": by_model, "by_day": by_day}
    home.mkdir(parents=True, exist_ok=True)
    (home / "baseline.json").write_text(json.dumps(base), encoding="utf-8")
    return base


def daily_by_model(entries: list, home: Path, t: float, cycle_day: int) -> dict:
    """{day: {model: usd}} for this cycle: the seeded CSV's days plus the hook's estimates after it."""
    start = cycle_start(t, cycle_day)
    base = load_json(home / "baseline.json", {})
    seeded = base.get("cycle_start") == start
    days = {d: dict(models) for d, models in (base.get("by_day") or {}).items()} if seeded else {}
    until = float(base.get("until", 0)) if seeded else 0.0
    for e in entries:
        if e["t"] > until and day(e["t"]) >= start:
            cell = days.setdefault(day(e["t"]), {})
            model = e.get("model") or "?"
            cell[model] = round(cell.get(model, 0.0) + e["usd"], 4)
    return days
