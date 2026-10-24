"""cbudget: hook, report, allow, install-hook."""
from __future__ import annotations

import argparse
import collections
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from . import budget

HOOKS = Path.home() / ".cursor" / "hooks.json"
EVENTS = ("stop", "beforeSubmitPrompt")


def notify(message: str) -> None:
    """A macOS notification; a failure here must never reach Cursor."""
    script = f"display notification {json.dumps(message)} with title \"cursor-budget\""
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
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


def report(days: int, home: Path = budget.HOME) -> str:
    since = budget.day(time.time() - (days - 1) * 86400)
    entries = [e for e in budget.read_ledger(home) if budget.day(e["t"]) >= since]
    by_day, by_model, by_conv = collections.Counter(), collections.Counter(), collections.Counter()
    for e in entries:
        by_day[budget.day(e["t"])] += e["usd"]
        by_model[e.get("model")] += e["usd"]
        by_conv[e.get("conversation_id")] += e["usd"]
    cfg, now = budget.config(home), time.time()
    cycle, seeded = budget.cycle_total(budget.read_ledger(home), home, now, cfg["cycle_day"])
    note = "cobrado según el CSV + estimado desde entonces" if seeded else "solo estimado; sin `cbudget seed`"
    lines = [f"ciclo desde {budget.cycle_start(now, cfg['cycle_day'])}: ${cycle:.2f} de ${cfg['cycle_limit']:.0f} "
             f"({cycle / cfg['cycle_limit']:.0%}), quedan ${cfg['cycle_limit'] - cycle:.2f}  [{note}]", "",
             f"turnos: {len(entries)}  total estimado: ${sum(by_day.values()):.2f}", "", "por día:"]
    lines += [f"  {d}  ${v:.2f}" for d, v in sorted(by_day.items())]
    lines += ["", "por modelo:"] + [f"  {m}  ${v:.2f}" for m, v in by_model.most_common()]
    lines += ["", "chats más caros:"] + [f"  {c}  ${v:.2f}" for c, v in by_conv.most_common(5)]
    return "\n".join(lines)


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
    """Add `command` to stop and beforeSubmitPrompt, keeping every other hook. True when the file changed."""
    data = budget.load_json(path, {}) or {"version": 1}
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    changed = False
    for event in EVENTS:
        entries = hooks.setdefault(event, [])
        if not any(_ours(e) for e in entries):
            entries.append({"command": command, "timeout": 10})
            changed = True
    if changed:
        if path.exists():
            path.with_name(path.name + f".bak-{time.strftime('%Y-%m-%d-%H%M%S')}").write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return changed


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="cbudget", description="Cursor spend: ledger, warnings and blocks.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hook", help="Cursor hook entry point (stdin: the hook payload)")
    rep = sub.add_parser("report", help="estimated spend from the ledger")
    rep.add_argument("--days", type=int, default=7)
    alw = sub.add_parser("allow", help="lift blocks for a while")
    alw.add_argument("--minutes", type=int, default=30)
    cal = sub.add_parser("calibrate", help="billed vs estimated per model, from Cursor's usage CSV")
    cal.add_argument("csv", type=Path)
    sed = sub.add_parser("seed", help="take this cycle's billed spend from Cursor's usage CSV")
    sed.add_argument("csv", type=Path)
    sub.add_parser("install-hook", help="add cbudget to ~/.cursor/hooks.json")
    args = parser.parse_args(argv)

    if args.cmd == "hook":
        print(json.dumps(hook(sys.stdin.read())))
        return 0
    if args.cmd == "report":
        print(report(args.days))
        return 0
    if args.cmd == "calibrate":
        print(calibrate_report(args.csv))
        return 0
    if args.cmd == "seed":
        import csv
        with args.csv.open(newline="", encoding="utf-8") as fh:
            base = budget.seed(csv.DictReader(fh))
        print(f"ciclo desde {base['cycle_start']}: ${base['billed']:.2f} cobrado según el CSV")
        return 0
    if args.cmd == "allow":
        until = budget.allow(args.minutes)
        print(f"bloqueos levantados hasta las {time.strftime('%H:%M', time.localtime(until))}")
        return 0
    entry = Path(__file__).resolve().parent.parent / "cbudget.py"
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(entry))} hook"
    print("hook instalado" if install_hook(command) else "el hook ya estaba instalado")
    return 0
