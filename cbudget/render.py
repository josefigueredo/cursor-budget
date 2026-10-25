"""The `cbudget report` screen, modeled on Cursor's Usage page: a monthly bar, stat cards, per-model budgets,
spend per day stacked by model, and the latest turns. Truecolor ANSI when the terminal takes it."""
from __future__ import annotations

import datetime
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

from . import budget

ANSI = re.compile(r"\x1b\[[0-9;]*m")
MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")

# Cursor's Usage page palette
TEXT, MUTED, BORDER, TRACK, ACCENT = (236, 236, 236), (140, 140, 140), (58, 58, 58), (48, 48, 48), (122, 162, 204)
GREEN, AMBER, RED = (22, 160, 92), (246, 196, 120), (232, 96, 80)
MODEL_COLORS = (("claude-opus", (22, 160, 92)), ("claude-sonnet", (62, 152, 255)), ("claude-haiku", (160, 210, 90)),
                ("gpt", (110, 224, 208)), ("composer", (160, 124, 245)), ("auto", (246, 196, 120)),
                ("default", (246, 196, 120)), ("grok", (47, 111, 214)), ("gemini", (236, 120, 180)),
                ("usage-correction", (242, 120, 58)))


def color_enabled(stream=sys.stdout) -> bool:
    """NO_COLOR (no-color.org) and pipes get plain text."""
    return "NO_COLOR" not in os.environ and hasattr(stream, "isatty") and stream.isatty()


class Paint:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def __call__(self, text: str, rgb: Optional[tuple] = None, bold: bool = False) -> str:
        if not self.enabled or (rgb is None and not bold):
            return text
        codes = (["1"] if bold else []) + ([f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}"] if rgb else [])
        return f"\033[{';'.join(codes)}m{text}\033[0m"


def width(text: str) -> int:
    return len(ANSI.sub("", text))


def pad(text: str, size: int) -> str:
    return text + " " * max(size - width(text), 0)


def model_color(model: str) -> tuple:
    return next((rgb for prefix, rgb in MODEL_COLORS if model.startswith(prefix)), (150, 150, 150))


def level(share: float) -> tuple:
    return GREEN if share < 0.75 else AMBER if share < 0.9 else RED


def money(usd: float) -> str:
    return f"${usd:,.2f}"


def tokens(n: float) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}K" if n >= 1e3 else f"{int(n)}"


def short_date(d: datetime.date) -> str:
    return f"{d.day:02d} {MONTHS[d.month - 1]}"


def long_date(d: datetime.date) -> str:
    return f"{d.day} de {MONTHS[d.month - 1]} de {d.year}"


def next_cycle(start: str, cycle_day: int) -> datetime.date:
    """31 days after a cycle starts always falls inside the next cycle, whatever the month lengths."""
    later = datetime.datetime.combine(datetime.date.fromisoformat(start) + datetime.timedelta(days=31),
                                      datetime.time(12))
    return datetime.date.fromisoformat(budget.cycle_start(later.timestamp(), cycle_day))


def line_bar(share: float, size: int, paint: Paint, rgb: tuple) -> str:
    filled = min(int(round(share * size)), size)
    return paint("━" * filled, rgb) + paint("━" * (size - filled), TRACK)


def card(title: str, body: list, size: int, paint: Paint) -> list:
    inner = size - 4
    top = paint("╭" + "─" * (size - 2) + "╮", BORDER)
    bottom = paint("╰" + "─" * (size - 2) + "╯", BORDER)
    side = paint("│", BORDER)
    rows = ([paint(title, MUTED)] if title else []) + body
    return [top] + [f"{side} {pad(r, inner)} {side}" for r in rows] + [bottom]


def side_by_side(cards: list, gap: int = 2) -> list:
    height = max(len(c) for c in cards)
    widths = [width(c[0]) for c in cards]
    rows = []
    for i in range(height):
        rows.append((" " * gap).join(pad(c[i], w) if i < len(c) else " " * w for c, w in zip(cards, widths)))
    return rows


def report(days: int, home: Path = budget.HOME, color: bool = False, now: Optional[float] = None,
           columns: Optional[int] = None) -> str:
    paint, now = Paint(color), time.time() if now is None else now
    size = max(80, min(columns or shutil.get_terminal_size((100, 40)).columns, 110))
    cfg, ledger = budget.config(home), budget.read_ledger(home)
    start = budget.cycle_start(now, cfg["cycle_day"])
    reset = next_cycle(start, cfg["cycle_day"])
    today = datetime.date.fromtimestamp(now)
    cycle, seeded = budget.cycle_total(ledger, home, now, cfg["cycle_day"])
    limit = budget.cycle_cap(cfg, home, now)
    extras = budget.extras_granted(home, now, cfg["cycle_day"])
    share = cycle / limit if limit else 0.0
    out = ["", " " + paint("Uso de Cursor", TEXT, bold=True)]

    # Monthly usage
    base_source = budget.load_json(home / "baseline.json", {}).get("source")
    source = ("Lo que mostraba Cursor al cargarlo, más lo estimado desde entonces." if seeded and base_source == "amount"
              else "Cobrado según el CSV, más lo estimado desde entonces." if seeded
              else "Solo lo visto desde la instalación: `cbudget seed --amount <usd>`.")
    out += card("Uso del ciclo", [
        paint(money(cycle), TEXT, bold=True) + paint(f" / {money(limit)}", MUTED),
        line_bar(share, size - 4, paint, ACCENT if share < 0.9 else RED),
        paint(f"Se reinicia el {long_date(reset)}  ·  Quedan {(reset - today).days} días", MUTED),
        paint(source, MUTED)]
        + ([paint("Tope del plan " + money(float(cfg["cycle_limit"]))
                  + "".join(" + extra " + money(usd) for usd in extras), MUTED)] if extras else []), size, paint)

    # Stat cards
    today_usd = sum(budget.daily_by_model(ledger, home, now, cfg["cycle_day"]).get(today.isoformat(), {}).values())
    third = (size - 4) // 3
    out += side_by_side([
        card("Total del ciclo", [paint(money(cycle), TEXT, bold=True)], third, paint),
        card("Disponible", [paint(money(limit - cycle), level(share), bold=True)], third, paint),
        card("Hoy", [paint(money(today_usd), TEXT, bold=True)], size - 2 * third - 4, paint)])

    # Budgets per model
    if cfg["model_budgets"]:
        name_w = max(len(p) for p in cfg["model_budgets"]) + 2
        bar_w = size - 4 - name_w - 30
        rows = []
        for pattern, cap in cfg["model_budgets"].items():
            cap = float(cap)
            spent = budget.model_spend(ledger, home, now, cfg["cycle_day"], pattern)
            used = spent / cap if cap else 0.0
            rows.append(paint("● ", model_color(pattern)) + pad(paint(pattern, TEXT), name_w)
                        + paint(f"{money(spent):>9s}", TEXT) + paint(f" / {money(cap):<9s}", MUTED) + "  "
                        + line_bar(used, bar_w, paint, level(used)) + paint(f"{used:5.0%}", level(used), bold=True))
        out += card("Presupuesto por modelo", rows, size, paint)

    # Spend per day, stacked by model
    series = budget.daily_by_model(ledger, home, now, cfg["cycle_day"])
    if series:
        top = max(sum(m.values()) for m in series.values()) or 1.0
        bar_w = size - 4 - 8 - 11
        rows = [paint("Tu uso por día en este ciclo", MUTED), ""]
        seen = {}
        for d in sorted(series):
            models = sorted(series[d].items(), key=lambda kv: -kv[1])
            total, cells = sum(series[d].values()), ""
            for model, usd in models:
                seen[model] = seen.get(model, 0.0) + usd
                cells += paint("█" * int(round(usd / top * bar_w)), model_color(model))
            if not width(cells) and total > 0:  # under one cell: a sliver, so the day still shows
                cells = paint("▏", model_color(models[0][0]))
            label = short_date(datetime.date.fromisoformat(d))
            rows.append(paint(f"{label}  ", MUTED) + pad(cells, bar_w) + paint(f"{money(total):>11s}", TEXT))
        legend, line = [], ""
        for model, _ in sorted(seen.items(), key=lambda kv: -kv[1]):
            item = paint("● ", model_color(model)) + paint(model, MUTED) + "   "
            if width(line) + width(item) > size - 4:
                legend.append(line)
                line = ""
            line += item
        rows += [""] + legend + [line]
        out += card("Uso por día", rows, size, paint)

    # Latest turns
    since = now - days * 86400
    turns = sorted((e for e in ledger if e["t"] >= since), key=lambda e: -e["t"])[:12]
    head = f"{'Fecha':<15s}  {'Modelo':<26s}{'Chat':<10s}{'Tokens':>9s}{'Costo':>10s}"
    rows = [paint(head, MUTED)]
    for e in turns:
        when = datetime.datetime.fromtimestamp(e["t"])
        total_tokens = (e.get("input_tokens") or 0) + (e.get("output_tokens") or 0)
        rows.append(paint(f"{short_date(when.date())}, {when:%H:%M}  ", TEXT)
                    + paint("● ", model_color(e.get("model") or "")) + pad(paint((e.get("model") or "?")[:24], TEXT), 26)
                    + paint(f"{(e.get('conversation_id') or '?')[:8]:<10s}", MUTED)
                    + paint(f"{tokens(total_tokens):>9s}", TEXT) + paint(f"{money(e['usd']):>10s}", TEXT))
    if len(rows) == 1:
        rows.append(paint("Sin turnos registrados en el período.", MUTED))
    title = "Últimos turnos (último día)" if days == 1 else f"Últimos turnos ({days} días, costo estimado)"
    out += card(title, rows, size, paint)
    return "\n".join(out) + "\n"
