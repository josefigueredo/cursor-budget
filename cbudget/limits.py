"""`cbudget limits`: show and check the limits, change them by flags (scripts, agents) or interactively (terminal)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from . import budget
from .render import AMBER, MUTED, RED, TEXT, GREEN, Paint, card, level, line_bar, model_color, money, pad


def describe(key: str, value) -> str:
    kind = budget.SCALARS[key][0]
    if kind == "usd":
        return money(float(value))
    if kind == "share":
        return f"{float(value):.0%}"
    if kind == "bool":
        return "sí" if value else "no"
    return str(value)


def show(home: Path = budget.HOME, color: bool = False, now: Optional[float] = None, size: int = 100) -> str:
    paint, now = Paint(color), time.time() if now is None else now
    cfg, ledger = budget.config(home), budget.read_ledger(home)
    custom = budget.user_config(home)
    out = ["", " " + paint("Límites de cursor-budget", TEXT, bold=True)]

    rows = [paint(f"{'Límite':<22s}{'Valor':>10s}   Qué hace", MUTED)]
    for key, (_, help_text) in budget.SCALARS.items():
        mark = paint(" *", AMBER) if key in custom else "  "
        rows.append(pad(paint(key, TEXT), 22) + paint(f"{describe(key, cfg[key]):>10s}", TEXT, bold=True) + mark
                    + " " + paint(help_text, MUTED))
    rows += ["", paint("* cambiado en ~/.cursor/budget/config.json; el resto es el valor por defecto", MUTED)]
    out += card("Generales", rows, size, paint)

    granted = budget.extras_granted(home, now, cfg["cycle_day"])
    rows = []
    for i, usd in enumerate(cfg["cycle_extras"]):
        state = paint("registrado", GREEN) if i < len(granted) else paint("sin registrar: `cbudget extra`", MUTED)
        rows.append(pad(paint(budget.extra_name(i).capitalize(), TEXT), 22)
                    + paint(f"{money(float(usd)):>10s}", TEXT, bold=True) + "   " + state)
    rows += ["", paint(f"Tope del ciclo: {money(budget.cycle_cap(cfg, home, now))}. Se registran cuando te los "
                       f"otorgan y terminan con el ciclo.", MUTED)]
    out += card("Extras del plan (ciclo)", rows, size, paint)

    budgets = cfg["model_budgets"]
    if budgets:
        width = max(len(p) for p in budgets) + 2
        bar_w = size - 4 - width - 30
        rows = []
        for pattern, cap in budgets.items():
            cap = float(cap)
            spent = budget.model_spend(ledger, home, now, cfg["cycle_day"], pattern)
            used = spent / cap if cap else 0.0
            rows.append(paint("● ", model_color(pattern)) + pad(paint(pattern, TEXT), width)
                        + paint(f"{money(spent):>9s}", TEXT) + paint(f" / {money(cap):<9s}", MUTED) + "  "
                        + line_bar(used, bar_w, paint, level(used)) + paint(f"{used:5.0%}", level(used), bold=True))
    else:
        rows = [paint("Sin presupuestos por modelo. Agregá uno con `cbudget limits model <patrón> <usd>`.", MUTED)]
    out += card("Presupuesto por modelo (ciclo)", rows, size, paint)

    blocked = cfg["blocked_models"]
    out += card("Modelos bloqueados", [paint(", ".join(blocked) if blocked else "Ninguno", TEXT)], size, paint)

    problems = budget.check_limits(cfg, budget.known_models(ledger, home), budget.cycle_cap(cfg, home, now))
    rows = [paint("● ", RED if lvl == "error" else AMBER) + paint(msg, TEXT) for lvl, msg in problems] or \
        [paint("● ", GREEN) + paint("Sin problemas: los límites son coherentes entre sí.", TEXT)]
    out += card("Chequeo", rows, size, paint)
    return "\n".join(out) + "\n"


def set_scalar(key: str, raw: str, home: Path = budget.HOME, actor: str = "cli", notify=lambda m: None) -> str:
    if key not in budget.SCALARS:
        raise ValueError(f"límite desconocido: {key}; usá uno de {', '.join(budget.SCALARS)}")
    value = budget.parse_value(key, raw)
    budget.save_config({key: value}, home, actor=actor, notify=notify)
    return f"{key} = {describe(key, value)}"


def set_model(pattern: str, raw: Optional[str], home: Path = budget.HOME, actor: str = "cli",
              notify=lambda m: None) -> str:
    budgets = dict(budget.config(home)["model_budgets"])
    if raw is None:
        if budgets.pop(pattern, None) is None:
            raise ValueError(f"{pattern} no tiene presupuesto")
        message = f"{pattern} sin presupuesto"
    else:
        budgets[pattern] = budget.parse_value("cycle_limit", raw)
        message = f"{pattern} = {money(budgets[pattern])} por ciclo"
    budget.save_config({"model_budgets": budgets}, home, actor=actor, notify=notify)
    return message


def set_blocked(pattern: str, blocked: bool, home: Path = budget.HOME, actor: str = "cli",
                notify=lambda m: None) -> str:
    current = list(budget.config(home)["blocked_models"])
    if blocked and pattern not in current:
        current.append(pattern)
    elif not blocked and pattern in current:
        current.remove(pattern)
    else:
        return f"{pattern} ya estaba {'bloqueado' if blocked else 'desbloqueado'}"
    budget.save_config({"blocked_models": current}, home, actor=actor, notify=notify)
    return f"{pattern} {'bloqueado' if blocked else 'desbloqueado'}"


def edit(home: Path = budget.HOME, ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
         notify=lambda m: None) -> bool:
    """Walk every limit with its current value as the default; Enter keeps it. Nothing is saved until confirmed."""
    cfg = budget.config(home)
    changes = {}
    say("Enter deja el valor actual.\n")
    for key, (_, help_text) in budget.SCALARS.items():
        while True:
            raw = ask(f"{help_text} ({key}) [{describe(key, cfg[key])}]: ").strip()
            if not raw:
                break
            try:
                value = budget.parse_value(key, raw)
            except ValueError as exc:
                say(f"  {exc}")
                continue
            if value != cfg[key]:
                changes[key] = value
            break

    budgets = dict(cfg["model_budgets"])
    say("\nPresupuesto por modelo: un monto lo cambia, '-' lo quita.")
    for pattern in list(budgets):
        raw = ask(f"  {pattern} [{money(float(budgets[pattern]))}]: ").strip()
        if raw == "-":
            budgets.pop(pattern)
        elif raw:
            try:
                budgets[pattern] = budget.parse_value("cycle_limit", raw)
            except ValueError as exc:
                say(f"  {exc}; queda igual")
    while True:
        pattern = ask("  Nuevo patrón (Enter para terminar, p. ej. composer-*): ").strip()
        if not pattern:
            break
        try:
            budgets[pattern] = budget.parse_value("cycle_limit", ask(f"  Presupuesto para {pattern}: "))
        except ValueError as exc:
            say(f"  {exc}; no se agregó")
    if budgets != cfg["model_budgets"]:
        changes["model_budgets"] = budgets

    blocked = list(cfg["blocked_models"])
    raw = ask(f"\nModelos bloqueados, separados por coma [{', '.join(blocked) or 'ninguno'}]: ").strip()
    if raw:
        blocked = [] if raw == "-" else [p.strip() for p in raw.split(",") if p.strip()]
        if blocked != cfg["blocked_models"]:
            changes["blocked_models"] = blocked

    if not changes:
        say("\nSin cambios.")
        return False
    say("\nCambios:")
    for key, value in changes.items():
        before = cfg[key]
        if key in budget.SCALARS:
            say(f"  {key}: {describe(key, before)} -> {describe(key, value)}")
        elif key == "model_budgets":
            for pattern in [*before, *(p for p in value if p not in before)]:
                old, new = before.get(pattern), value.get(pattern)
                if old != new:
                    say(f"  {pattern}: {money(float(old)) if old is not None else 'sin presupuesto'} -> "
                        f"{money(float(new)) if new is not None else 'sin presupuesto'}")
        else:
            say(f"  bloqueados: {', '.join(before) or 'ninguno'} -> {', '.join(value) or 'ninguno'}")
    merged = {**cfg, **changes}
    cap = budget.cycle_cap(merged, home, time.time())
    for lvl, msg in budget.check_limits(merged, budget.known_models(budget.read_ledger(home), home), cap):
        say(f"  {'error' if lvl == 'error' else 'aviso'}: {msg}")
    if ask("¿Guardar? [s/N]: ").strip().lower() not in ("s", "si", "sí", "y", "yes"):
        say("No se guardó nada.")
        return False
    budget.save_config(changes, home, actor="terminal", notify=notify)
    say("Guardado.")
    return True
