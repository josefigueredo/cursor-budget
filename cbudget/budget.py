"""Cost of a Cursor turn, the local ledger, and the warn and block decisions.

Cursor's `stop` payload carries model, input_tokens, output_tokens, cache_read_tokens and cache_write_tokens per turn
(measured 2026-10-24 with cursor-agent 2026.10.22). input_tokens includes the cached ones: a second turn reported
22934 input with 19807 cache_read. So the uncached input is input - cache_read - cache_write.
"""
from __future__ import annotations

import calendar
import contextlib
import datetime
import fcntl
import fnmatch
import json
import math
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
    "cycle_limit": 150.0,
    "cycle_extras": [75.0, 75.0],
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


def read_json_strict(path: Path) -> dict:
    """A JSON object another tool owns (hooks.json, cli-config.json): raise ValueError, in Spanish and with the line,
    instead of falling back, so a rewrite never drops what it could not read."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} no es JSON válido (línea {exc.lineno}: {exc.msg}); no lo toqué") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} no es un objeto JSON; no lo toqué")
    return data


def write_json_atomic(path: Path, data: dict) -> None:
    """Write through a temporary file in the same folder and rename it over `path`: a reader sees the old file or the
    new one, never half of it. Keeps the file's permissions."""
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else None
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def locked(home: Path):
    """One process at a time on the shared state: turns that end together each see the others' entries, so a limit
    crossed between them is warned once, and two changes at once never lose one."""
    home.mkdir(parents=True, exist_ok=True)
    with (home / ".lock").open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0


def _valid(key: str, value) -> bool:
    """Whether a config.json value has the shape its default has; a wrong one falls back to the default."""
    if key in ("blocked_models",):
        return isinstance(value, list) and all(isinstance(p, str) for p in value)
    if key in ("cycle_extras", "cycle_warn"):
        return isinstance(value, list) and all(_number(v) for v in value) and (key != "cycle_warn" or bool(value))
    if key == "model_budgets":
        return isinstance(value, dict) and all(isinstance(k, str) and _number(v) for k, v in value.items())
    kind = SCALARS.get(key, ("", ""))[0]
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "day":
        return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 31
    return _number(value) and (kind != "share" or value <= 1)


def config_problems(home: Path = HOME) -> list:
    """What is wrong with config.json itself, as error messages: unreadable, not an object, or a value of the wrong
    type. The hooks keep running on the defaults meanwhile, so the person has to hear about it."""
    path = home / "config.json"
    if not path.exists():
        return []
    try:
        data = read_json_strict(path)
    except (OSError, ValueError) as exc:
        return [f"config.json no se puede leer y rige la configuración por defecto: {exc}"]
    return [f"config.json: {key} = {value!r} no es un valor válido; rige el por defecto ({DEFAULTS[key]!r})"
            for key, value in data.items() if key in DEFAULTS and not _valid(key, value)]


def config(home: Path = HOME) -> dict:
    data = load_json(home / "config.json", {})
    if not isinstance(data, dict):
        data = {}
    return {**DEFAULTS, **{k: v for k, v in data.items() if k not in DEFAULTS or _valid(k, v)}}


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
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and _number(entry.get("t")) and _number(entry.get("usd")):
            entries.append(entry)
    return entries


def totals(entries: list, conversation: Optional[str], today: str) -> tuple:
    conv = sum(e["usd"] for e in entries if conversation and e.get("conversation_id") == conversation)
    daily = sum(e["usd"] for e in entries if day(e["t"]) == today)
    return round(conv, 4), round(daily, 4)


def cycle_start(t: float, cycle_day: int) -> str:
    """The date the billing cycle containing t started: day `cycle_day` of this month or the previous one. A month
    without that day starts it on its last day (a 31st cycle starts on Feb 28), like render.next_cycle."""
    d = datetime.date.fromtimestamp(t)
    this = min(cycle_day, calendar.monthrange(d.year, d.month)[1])
    if d.day >= this:
        return d.replace(day=this).isoformat()
    prev = d.replace(day=1) - datetime.timedelta(days=1)
    return prev.replace(day=min(cycle_day, prev.day)).isoformat()


def cycle_total(entries: list, home: Path, t: float, cycle_day: int) -> tuple:
    """Spend this cycle: what Cursor billed up to the seeded CSV, plus the hook's estimates after it.
    Returns (usd, complete): complete when a baseline for this cycle exists, or when the hooks were already counting
    before the cycle started (an earlier seed or turn), so a seed is needed only once, at install."""
    start = cycle_start(t, cycle_day)
    base = load_json(home / "baseline.json", {})
    seeded = base.get("cycle_start") == start
    billed, until = (float(base.get("billed", 0)), float(base.get("until", 0))) if seeded else (0.0, 0.0)
    estimated = sum(e["usd"] for e in entries if e["t"] > until and day(e["t"]) >= start)
    counting_before = str(base.get("cycle_start") or "9999") < start or any(day(e["t"]) < start for e in entries)
    return round(billed + estimated, 4), seeded or counting_before


EXTRA_NAMES = ("el extra", "el extra excepcional")


def extras_granted(home: Path, t: float, cycle_day: int) -> list:
    """The extras registered for the cycle containing t, in order. They end with their cycle."""
    state = load_json(home / "extras.json", {})
    return list(state.get("granted") or []) if state.get("cycle_start") == cycle_start(t, cycle_day) else []


def cycle_cap(cfg: dict, home: Path, t: float) -> float:
    """The plan cap plus the extras granted this cycle."""
    return round(float(cfg["cycle_limit"]) + sum(extras_granted(home, t, cfg["cycle_day"])), 2)


def extra_name(i: int) -> str:
    return EXTRA_NAMES[i] if i < len(EXTRA_NAMES) else "otro extra"


def extra_hint(cfg: dict, home: Path, t: float) -> str:
    """What to do when the cycle reaches its cap: ask for the next extra, or say none is left."""
    i, extras = len(extras_granted(home, t, cfg["cycle_day"])), cfg["cycle_extras"]
    if i < len(extras):
        return f" Podés pedir {extra_name(i)} de ${float(extras[i]):.0f} y registrarlo con `cbudget extra`."
    return f" No quedan extras: ya usaste los {len(extras)} extras del ciclo."


def grant_extra(home: Path = HOME, now: Callable[[], float] = time.time, actor: str = "cli",
                notify: Callable[[str], None] = lambda msg: None) -> dict:
    """Register the next extra of the plan for this cycle, once it was granted. Logged and notified like a limit."""
    t, cfg = now(), config(home)
    with locked(home):
        granted, extras = extras_granted(home, t, cfg["cycle_day"]), cfg["cycle_extras"]
        if len(granted) >= len(extras):
            raise ValueError(f"ya se registraron los {len(extras)} extras de este ciclo: el tope es "
                             f"${cycle_cap(cfg, home, t):.2f} hasta que se reinicie")
        name, usd, before = extra_name(len(granted)), float(extras[len(granted)]), cycle_cap(cfg, home, t)
        write_json_atomic(home / "extras.json", {"cycle_start": cycle_start(t, cfg["cycle_day"]),
                                                 "granted": granted + [usd]})
        after = cycle_cap(cfg, home, t)
        with (home / "changes.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": round(t), "actor": actor, "key": "cycle_extra", "before": before,
                                 "after": after}, ensure_ascii=False) + "\n")
    notify(f"Cursor: {name} de ${usd:.0f} registrado ({actor}): el tope del ciclo pasa a ${after:.0f}.")
    return {"name": name, "usd": usd, "before": before, "after": after}


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
    """Record the turn and warn once when a conversation or the day crosses its warn or block limit. Reading the
    ledger, appending the turn and deciding the warnings happen under the lock, so turns that end together each see
    the others and a limit crossed between them is warned exactly once."""
    cfg, table, t = config(home), prices(home), now()
    usd = cost(payload, table)
    said = []
    say = said.append
    with locked(home):
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
        cycle_before, limit = round(cycle_after - usd, 4), cycle_cap(cfg, home, t)
        since = "" if seeded else " desde que se instaló; cargá lo gastado con `cbudget seed --amount <usd>`"
        for share in sorted(cfg["cycle_warn"], reverse=True):
            if crossed(cycle_before, cycle_after, limit * share):
                hint = extra_hint(cfg, home, t) if share >= 1 else ""
                say(f"Cursor: el ciclo ya va ~${cycle_after:.2f} de ${limit:.0f} ({cycle_after / limit:.0%}){since}.{hint}")
                break
        else:
            if cfg["notify_each_turn"]:
                say(f"turno ~${usd:.2f} · chat ~${conv_after:.2f} · ciclo ~${cycle_after:.2f} de ${limit:.0f}{since}")
        pattern = budget_for(payload.get("model") or "", cfg["model_budgets"])
        if pattern:
            cap = float(cfg["model_budgets"][pattern])
            spent = model_spend(entries + [entry], home, t, cfg["cycle_day"], pattern)
            for share in (1.0, cfg["model_warn"]):
                if crossed(spent - usd, spent, cap * share):
                    say(f"Cursor: {pattern} ya usó ~${spent:.2f} de sus ${cap:.0f} del ciclo ({spent / cap:.0%}).")
                    break
        for limit_key, before, after, what in (
                ("block_conversation", conv_before, conv_after, "este chat"),
                ("warn_conversation", conv_before, conv_after, "este chat"),
                ("block_day", day_before, day_after, "hoy"),
                ("warn_day", day_before, day_after, "hoy")):
            if crossed(before, after, cfg[limit_key]):
                tail = " Abrí un chat nuevo." if what == "este chat" else ""
                say(f"Cursor: {what} ya va ~${after:.2f} (límite {limit_key} ${cfg[limit_key]:.0f}).{tail}")
                break
    for message in said:  # outside the lock: a slow notification must not hold up other turns
        notify(message)
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
                                 "reason": reason}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    notify(f"Cursor bloqueado: {reason}")
    return {"continue": False, "user_message": f"cursor-budget: {reason}"}


def allow(minutes: int, home: Path = HOME, now: Callable[[], float] = time.time) -> float:
    until = now() + minutes * 60
    home.mkdir(parents=True, exist_ok=True)
    write_json_atomic(home / "allow.json", {"until": until})
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
    write_json_atomic(home / "baseline.json", base)
    return base


def seed_amount(usd: float, home: Path = HOME, now: Callable[[], float] = time.time,
                cycle_day: Optional[int] = None) -> dict:
    """The no-CSV seed: the spend Cursor's Usage page shows right now. No per-model or per-day detail, so model
    budgets count only what the hooks see from here on."""
    t = now()
    base = {"cycle_start": cycle_start(t, cycle_day or config(home)["cycle_day"]), "billed": round(usd, 2),
            "until": t, "by_model": {}, "by_day": {}, "source": "amount"}
    home.mkdir(parents=True, exist_ok=True)
    write_json_atomic(home / "baseline.json", base)
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


# Limits: one place that knows their types, so the check, `limits set` and the interactive editor agree.
SCALARS = {
    "warn_conversation": ("usd", "Avisa cuando un chat pasa este monto"),
    "block_conversation": ("usd", "Bloquea un chat que llegó a este monto"),
    "warn_day": ("usd", "Avisa cuando el día pasa este monto"),
    "block_day": ("usd", "Bloquea el resto del día al llegar a este monto"),
    "cycle_limit": ("usd", "Tope del plan en el ciclo"),
    "cycle_day": ("day", "Día del mes en que se reinicia el ciclo"),
    "model_warn": ("share", "Parte del presupuesto de un modelo que dispara el aviso"),
    "notify_each_turn": ("bool", "Notificación después de cada turno"),
}


def parse_value(key: str, raw: str):
    kind = SCALARS[key][0]
    text = str(raw).strip().lstrip("$").replace(",", ".")
    if kind == "bool":
        if text.lower() in ("si", "sí", "s", "yes", "y", "true", "1", "on"):
            return True
        if text.lower() in ("no", "n", "false", "0", "off"):
            return False
        raise ValueError(f"{key}: esperaba sí o no, llegó {raw!r}")
    try:
        value = int(text) if kind == "day" else float(text.rstrip("%")) / (100 if text.endswith("%") else 1)
    except ValueError:
        raise ValueError(f"{key}: esperaba {'un día' if kind == 'day' else 'un número'}, llegó {raw!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"{key}: esperaba un número, llegó {raw!r}")
    if kind == "day":
        if not 1 <= value <= 31:
            raise ValueError(f"{key}: el día va de 1 a 31")
        return value
    if value < 0 or kind == "share" and value > 1:
        raise ValueError(f"{key}: valor fuera de rango ({raw})")
    return value


def user_config(home: Path = HOME) -> dict:
    return load_json(home / "config.json", {})


def save_config(changes: dict, home: Path = HOME, now: Callable[[], float] = time.time, actor: str = "cli",
                notify: Callable[[str], None] = lambda msg: None) -> dict:
    """Merge `changes` into config.json, log each change to changes.jsonl and notify: an agent can raise its own
    budget, so every change must be visible to the person."""
    with locked(home):
        path = home / "config.json"
        # merging into a file that cannot be read would replace every other customization with defaults
        current = read_json_strict(path) if path.exists() else {}
        merged = {**current, **changes}
        if merged == current:
            return merged
        write_json_atomic(path, merged)
        effective_before = {**DEFAULTS, **current}
        with (home / "changes.jsonl").open("a", encoding="utf-8") as fh:
            for key, value in changes.items():
                if effective_before.get(key) != value:
                    fh.write(json.dumps({"t": round(now()), "actor": actor, "key": key, "before": effective_before.get(key),
                                         "after": value}, ensure_ascii=False) + "\n")
    notify(f"Límites de Cursor cambiados ({actor}): {', '.join(sorted(changes))}")
    return merged


def check_limits(cfg: dict, known_models: list, cap: Optional[float] = None, home: Optional[Path] = None) -> list:
    """Problems in the effective config, as (level, message), level in error|warning. `cap` is the cycle cap with
    the extras granted (cycle_cap); without it, the plan's cycle_limit. With `home`, also config.json's own problems
    (unreadable, or a value of the wrong type), which the defaults hide."""
    cap = float(cfg["cycle_limit"]) if cap is None else cap
    problems = [("error", msg) for msg in config_problems(home)] if home is not None else []
    unknown = sorted(set(cfg) - set(DEFAULTS))
    if unknown:
        problems.append(("warning", f"Claves desconocidas en config.json, se ignoran: {', '.join(unknown)}"))
    for warn, block in (("warn_conversation", "block_conversation"), ("warn_day", "block_day")):
        if float(cfg[warn]) >= float(cfg[block]):
            problems.append(("error", f"{warn} (${cfg[warn]:.2f}) no es menor que {block} (${cfg[block]:.2f}): "
                                      f"el bloqueo llega sin aviso previo"))
    if float(cfg["block_day"]) > cap:
        problems.append(("warning", "block_day supera el tope del ciclo: nunca va a frenar antes que Cursor"))
    budgets = cfg.get("model_budgets") or {}
    total = sum(float(v) for v in budgets.values())
    if total > cap:
        problems.append(("warning", f"Los presupuestos por modelo suman ${total:.2f}, más que el tope del ciclo "
                                    f"(${cap:.2f})"))
    for pattern in budgets:
        if known_models and not any(fnmatch.fnmatch(m, pattern) for m in known_models):
            problems.append(("warning", f"El patrón {pattern!r} no coincide con ningún modelo usado este ciclo"))
    patterns = list(budgets)
    for i, first in enumerate(patterns):
        for later in patterns[i + 1:]:
            shadowed = [m for m in known_models if fnmatch.fnmatch(m, later) and fnmatch.fnmatch(m, first)]
            if shadowed:
                problems.append(("warning", f"{later!r} queda tapado por {first!r} para {shadowed[0]}: gana el primero"))
    return problems


def known_models(entries: list, home: Path = HOME) -> list:
    base = load_json(home / "baseline.json", {})
    return sorted(set((base.get("by_model") or {})) | {e.get("model") for e in entries if e.get("model")})
