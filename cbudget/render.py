"""The `cbudget report` screen: sections, aligned tables and usage bars, in ANSI color when the terminal takes it."""
from __future__ import annotations

import collections
import datetime
import os
import sys
import time
from pathlib import Path

from . import budget

BAR = 24
CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34", "cyan": "36"}


def color_enabled(stream=sys.stdout) -> bool:
    """NO_COLOR (no-color.org) and pipes get plain text."""
    return "NO_COLOR" not in os.environ and hasattr(stream, "isatty") and stream.isatty()


class Paint:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles:
            return text
        return f"\033[{';'.join(CODES[s] for s in styles)}m{text}\033[0m"


def level(share: float) -> str:
    return "green" if share < 0.75 else "yellow" if share < 0.9 else "red"


def bar(share: float, paint: Paint) -> str:
    filled = min(int(round(share * BAR)), BAR)
    return paint("█" * filled, level(share)) + paint("░" * (BAR - filled), "dim")


def money(usd: float) -> str:
    return f"${usd:,.2f}"


def fecha(iso: str) -> str:
    return datetime.date.fromisoformat(iso).strftime("%d/%m/%Y")


def next_cycle(start: str, cycle_day: int) -> datetime.date:
    """31 days after a cycle starts always falls inside the next cycle, whatever the month lengths."""
    later = datetime.datetime.combine(datetime.date.fromisoformat(start) + datetime.timedelta(days=31),
                                      datetime.time(12))
    return datetime.date.fromisoformat(budget.cycle_start(later.timestamp(), cycle_day))


def section(title: str, paint: Paint) -> list:
    return ["", paint(title.upper(), "bold", "blue")]


def report(days: int, home: Path = budget.HOME, color: bool = False, now: float = None) -> str:
    paint, now = Paint(color), time.time() if now is None else now
    cfg, ledger = budget.config(home), budget.read_ledger(home)
    start = budget.cycle_start(now, cfg["cycle_day"])
    left_days = (next_cycle(start, cfg["cycle_day"]) - datetime.date.fromtimestamp(now)).days
    cycle, seeded = budget.cycle_total(ledger, home, now, cfg["cycle_day"])
    limit = float(cfg["cycle_limit"])
    share = cycle / limit if limit else 0.0

    lines = [paint("CURSOR BUDGET", "bold", "cyan") + paint(f"  ·  Ciclo desde el {fecha(start)}, "
                                                           f"quedan {left_days} días", "dim"),
             paint("─" * 64, "dim")]
    lines += section("Ciclo", paint)
    lines.append(f"  {paint(money(cycle), 'bold')} de {money(limit)}  {bar(share, paint)} "
                 f"{paint(f'{share:4.0%}', level(share), 'bold')}   Quedan {paint(money(limit - cycle), 'bold')}")
    source = ("Cobrado según el CSV, más lo estimado desde entonces." if seeded
              else "Solo estimado desde la instalación: corré `cbudget seed <csv>`.")
    lines.append("  " + paint(source, "dim"))

    if cfg["model_budgets"]:
        width = max(len(p) for p in cfg["model_budgets"]) + 2
        lines += section("Presupuesto por modelo", paint)
        lines.append(paint(f"  {'Modelo':{width}s}{'Gastado':>10s}{'Tope':>8s}   {'Uso':{BAR + 6}s}{'Quedan':>10s}",
                           "dim"))
        for pattern, cap in cfg["model_budgets"].items():
            cap = float(cap)
            spent = budget.model_spend(ledger, home, now, cfg["cycle_day"], pattern)
            used = spent / cap if cap else 0.0
            remaining = paint(f"{money(cap - spent):>10s}", "red" if cap - spent <= 0 else "bold")
            lines.append(f"  {pattern:{width}s}{money(spent):>10s}{money(cap):>8s}   {bar(used, paint)} "
                         f"{paint(f'{used:4.0%}', level(used))} {remaining}")

    since = budget.day(now - (days - 1) * 86400)
    entries = [e for e in ledger if budget.day(e["t"]) >= since]
    by_day, by_model = collections.Counter(), collections.Counter()
    by_conv, turns, last_model = collections.Counter(), collections.Counter(), {}
    for e in entries:
        by_day[budget.day(e["t"])] += e["usd"]
        by_model[e.get("model") or "?"] += e["usd"]
        conv = e.get("conversation_id") or "?"
        by_conv[conv] += e["usd"]
        turns[conv] += 1
        last_model[conv] = e.get("model") or "?"
    total = sum(by_day.values())
    title = "Último día" if days == 1 else f"Últimos {days} días"
    lines += section(title, paint)
    lines.append(f"  {len(entries)} turnos  ·  {paint(money(total), 'bold')} estimado")
    if not entries:
        return "\n".join(lines + ["  " + paint("Sin turnos registrados en el período.", "dim"), ""])

    lines += ["", paint("  Por día", "bold")]
    top = max(by_day.values()) or 1.0
    for d, v in sorted(by_day.items()):
        lines.append(f"    {fecha(d)}  {money(v):>9s}  {paint('█' * max(1, round(v / top * BAR)), 'cyan')}")

    width = max(len(m) for m in by_model) + 2
    lines += ["", paint("  Por modelo", "bold")]
    for m, v in by_model.most_common():
        lines.append(f"    {m:{width}s}{money(v):>9s}  {paint(f'{v / total:4.0%}' if total else '', 'dim')}")

    width = max(len(last_model[c]) for c, _ in by_conv.most_common(5)) + 2
    lines += ["", paint("  Chats más caros", "bold")]
    for conv, v in by_conv.most_common(5):
        lines.append(f"    {paint(conv[:8], 'cyan')}  {last_model[conv]:{width}s}{money(v):>9s}  "
                     f"{paint(f'{turns[conv]} turno' + ('s' if turns[conv] != 1 else ''), 'dim')}")
    return "\n".join(lines + [""])
