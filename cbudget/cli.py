"""cbudget: hook, report, allow, install-hook."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from . import budget, limits, render

HOOKS = Path.home() / ".cursor" / "hooks.json"
EVENTS = ("stop", "beforeSubmitPrompt")


OSASCRIPT = "/usr/bin/osascript"  # by full path: Cursor runs hooks with its own PATH


def notify(message: str) -> None:
    """A macOS notification; a failure here must never reach Cursor, but it is logged to notify-errors.log so a
    missing notification can be explained."""
    if os.environ.get("CURSOR_BUDGET_NO_NOTIFY"):
        return
    script = f"display notification {json.dumps(message)} with title \"cursor-budget\""
    try:
        done = subprocess.run([OSASCRIPT, "-e", script], capture_output=True, timeout=5)
        error = f"exit {done.returncode}: {done.stderr.decode(errors='replace').strip()}" if done.returncode else ""
    except (OSError, subprocess.SubprocessError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    if error:
        try:
            budget.HOME.mkdir(parents=True, exist_ok=True)
            with (budget.HOME / "notify-errors.log").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": round(time.time()), "message": message, "error": error},
                                    ensure_ascii=False) + "\n")
        except OSError:
            pass


def hook(raw: str) -> dict:
    """Fail open: a broken budget hook must not stop Cursor, so any error answers {}."""
    try:
        payload = json.loads(raw or "{}")
        event = payload.get("hook_event_name")
        if event == "stop":
            return budget.on_stop(payload, notify=notify)
        if event == "beforeSubmitPrompt":
            return budget.on_submit(payload, notify=notify)
    except Exception:  # noqa: BLE001 - see docstring
        pass
    return {}


def calibrate_report(csv_path: Path, tolerance: float = 0.10) -> str:
    import csv
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = budget.calibrate(csv.DictReader(fh), budget.prices())
    lines = [f"{'modelo':42s} {'eventos':>7s} {'cobrado':>9s} {'estimado':>9s} {'error':>6s}  precio usado"]
    billed = estimated = 0.0
    for model, n, b, e, prefix in rows:
        billed, estimated = billed + b, estimated + e
        err = (e - b) / b if b else 0.0
        flag = "  <- ajustar" if b >= 0.5 and abs(err) > tolerance or prefix == "UNKNOWN" and b > 0 else ""
        lines.append(f"{model:42s} {n:7d} {b:9.2f} {e:9.2f} {err:+6.0%}  {prefix}{flag}")
    lines.append(f"{'total':42s} {'':7s} {billed:9.2f} {estimated:9.2f}")
    return "\n".join(lines)


def _ours(entry: dict) -> bool:
    try:
        argv = shlex.split(str(entry.get("command", "")))
    except ValueError:
        return False
    return len(argv) >= 2 and argv[-1] == "hook" and any(Path(a).name == "cbudget.py" for a in argv[:-1])


def install_hook(command: str, path: Path = HOOKS) -> bool:
    """Add `command` to stop and beforeSubmitPrompt, keeping every other hook. An older cbudget entry, from another
    install folder, is pointed at `command` instead of duplicated. True when the file changed."""
    data = budget.load_json(path, {}) or {"version": 1}
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    changed = False
    for event in EVENTS:
        entries = hooks.setdefault(event, [])
        ours = [e for e in entries if _ours(e)]
        if not ours:
            entries.append({"command": command, "timeout": 10})
            changed = True
        for entry in ours:
            if entry.get("command") != command:
                entry["command"] = command
                changed = True
    if changed:
        if path.exists():
            path.with_name(path.name + f".bak-{time.strftime('%Y-%m-%d-%H%M%S')}").write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return changed


NOTIFY_HINT = ("Mandé una notificación de prueba. Si no la viste, los avisos no te van a llegar: abrí Configuración "
               "del Sistema → Notificaciones → Script Editor, activá «Permitir notificaciones» y probá de nuevo con "
               "`cbudget notify-test`.")

ALIAS_MARK = "# cursor-budget"


def rc_file() -> Path:
    shell = Path(os.environ.get("SHELL", "zsh")).name
    return Path.home() / (".bashrc" if shell == "bash" else ".zshrc")


def install_alias(entry: Path, rc: Path) -> bool:
    """Write `alias cbudget=...` into the shell rc, replacing an older cbudget alias. True when the file changed."""
    line = f"alias cbudget={shlex.quote(f'{sys.executable} {entry}')}  {ALIAS_MARK}"
    lines = rc.read_text(encoding="utf-8").splitlines() if rc.exists() else []
    kept = [ln for ln in lines if not ln.rstrip().endswith(ALIAS_MARK)]
    if line in lines and len(lines) - len(kept) == 1:
        return False
    rc.write_text("\n".join(kept + [line]) + "\n", encoding="utf-8")
    return True


def limits_command(args) -> int:
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    actor = args.by or ("terminal" if interactive else "agente")
    color = {"always": True, "never": False}.get(args.color, render.color_enabled())
    try:
        if args.action is None:
            print(limits.show(color=color))
        elif args.action == "check":
            problems = budget.check_limits(budget.config(), budget.known_models(budget.read_ledger()))
            for lvl, msg in problems:
                print(f"{lvl}: {msg}")
            print("ok" if not problems else f"{len(problems)} problema(s)")
            return 1 if any(lvl == "error" for lvl, _ in problems) else 0
        elif args.action == "edit":
            if not interactive:
                print("`limits edit` necesita una terminal; desde un agente usá `limits set`, `limits model`, "
                      "`limits block` o `limits unblock`", file=sys.stderr)
                return 2
            limits.edit(notify=notify)
        elif args.action == "set":
            print(limits.set_scalar(args.key, args.value, actor=actor, notify=notify))
        elif args.action == "model":
            if args.remove == (args.usd is not None):
                print("indicá un monto o --remove", file=sys.stderr)
                return 2
            print(limits.set_model(args.pattern, None if args.remove else args.usd, actor=actor, notify=notify))
        else:
            print(limits.set_blocked(args.pattern, args.action == "block", actor=actor, notify=notify))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="cbudget", description="Cursor spend: ledger, warnings and blocks.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hook", help="Cursor hook entry point (stdin: the hook payload)")
    rep = sub.add_parser("report", help="estimated spend from the ledger")
    rep.add_argument("--days", type=int, default=7)
    rep.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    alw = sub.add_parser("allow", help="lift blocks for a while")
    alw.add_argument("--minutes", type=int, default=30)
    cal = sub.add_parser("calibrate", help="billed vs estimated per model, from Cursor's usage CSV")
    cal.add_argument("csv", type=Path)
    sed = sub.add_parser("seed", help="this cycle's spend so far: from Cursor's usage CSV, or the amount its page shows")
    sed.add_argument("csv", type=Path, nargs="?")
    sed.add_argument("--amount", help="what Cursor's Usage page shows, e.g. 84.30")
    lim = sub.add_parser("limits", help="show, check and change the limits")
    lim.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    lim.add_argument("--by", default=None, help="who changes them, for changes.jsonl (default: terminal or agente)")
    lsub = lim.add_subparsers(dest="action")
    lsub.add_parser("check", help="only the problems; exit 1 when there is an error")
    lsub.add_parser("edit", help="interactive editor (terminal only)")
    lset = lsub.add_parser("set", help="change a general limit")
    lset.add_argument("key", choices=list(budget.SCALARS))
    lset.add_argument("value")
    lmod = lsub.add_parser("model", help="set or remove a model's budget for the cycle")
    lmod.add_argument("pattern")
    lmod.add_argument("usd", nargs="?")
    lmod.add_argument("--remove", action="store_true")
    for name in ("block", "unblock"):
        lsub.add_parser(name, help=f"{name} a model pattern").add_argument("pattern")
    ext = sub.add_parser("extra", help="register the plan's next extra for this cycle, once it was granted")
    ext.add_argument("--by", default=None, help="who registers it, for changes.jsonl (default: terminal or agente)")
    sub.add_parser("install-hook", help="add cbudget to ~/.cursor/hooks.json")
    sub.add_parser("alias", help="add `alias cbudget=...` to ~/.zshrc (or ~/.bashrc)")
    sub.add_parser("notify-test", help="send a test notification, to check that macOS shows them")
    args = parser.parse_args(argv)

    if args.cmd == "hook":
        print(json.dumps(hook(sys.stdin.read())))
        return 0
    if args.cmd == "report":
        color = {"always": True, "never": False}.get(args.color, render.color_enabled())
        print(render.report(args.days, color=color))
        return 0
    if args.cmd == "calibrate":
        print(calibrate_report(args.csv))
        return 0
    if args.cmd == "seed":
        if (args.csv is None) == (args.amount is None):
            print("indicá el CSV o --amount con lo que muestra la página de uso de Cursor", file=sys.stderr)
            return 2
        if args.amount is not None:
            try:
                base = budget.seed_amount(budget.parse_value("cycle_limit", args.amount))
            except ValueError as exc:
                print(exc, file=sys.stderr)
                return 2
            print(f"Ciclo desde {base['cycle_start']}: ${base['billed']:.2f} según la página de Cursor. "
                  f"Desde ahora suma lo que vean los hooks.")
            return 0
        import csv
        with args.csv.open(newline="", encoding="utf-8") as fh:
            base = budget.seed(csv.DictReader(fh))
        print(f"Ciclo desde {base['cycle_start']}: ${base['billed']:.2f} cobrado según el CSV")
        return 0
    if args.cmd == "allow":
        until = budget.allow(args.minutes)
        print(f"bloqueos levantados hasta las {time.strftime('%H:%M', time.localtime(until))}")
        return 0
    if args.cmd == "limits":
        return limits_command(args)
    if args.cmd == "extra":
        actor = args.by or ("terminal" if sys.stdin.isatty() and sys.stdout.isatty() else "agente")
        try:
            got = budget.grant_extra(home=budget.HOME, actor=actor, notify=notify)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"{got['name'].capitalize()} de ${got['usd']:.0f} quedó registrado: el tope del ciclo pasa de "
              f"${got['before']:.2f} a ${got['after']:.2f}.")
        return 0
    if args.cmd == "notify-test":
        notify("Prueba: así se ven los avisos de gasto de Cursor.")
        print(NOTIFY_HINT)
        return 0
    entry = Path(__file__).resolve().parent.parent / "cbudget.py"
    if args.cmd == "alias":
        rc = rc_file()
        print(f"alias agregado en {rc}; abrí otra terminal o corré: source {rc}" if install_alias(entry, rc)
              else f"el alias ya estaba en {rc}")
        return 0
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(entry))} hook"
    print("hook instalado" if install_hook(command) else "el hook ya estaba instalado")
    return 0
