"""Render `cbudget report` with made-up data, as the HTML page behind docs/report.png.
  python3 docs/demo.py docs/report.html
Builds a throwaway budget folder (seeded cycle, model budgets, a few turns today) so the capture shows nobody's real
spend. Screenshot the page at 900 px wide to refresh docs/report.png."""
from __future__ import annotations

import html
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cbudget import budget, render  # noqa: E402

NOW = 1792850000.0  # 2026-10-24 10:53 -03, the tests' clock
# (day of October, model, usd): an invented cycle, about $84 before today
DAYS = [(2, "claude-sonnet-5-5-high", 3.10), (3, "auto", 2.40), (6, "claude-sonnet-5-5-high", 6.80),
        (7, "composer-2.5", 0.90), (8, "gpt-5.6-terra-medium", 1.70), (9, "claude-opus-5-5-high", 9.60),
        (10, "auto", 3.30), (13, "claude-sonnet-5-5-high", 5.20), (14, "auto", 4.10),
        (15, "claude-sonnet-5-5-high", 7.40), (16, "claude-opus-5-5-high", 12.80), (17, "auto", 2.20),
        (20, "claude-sonnet-5-5-high", 4.60), (21, "gpt-5.6-terra-medium", 2.30), (21, "claude-opus-5-5-high", 8.90),
        (22, "auto", 3.50), (23, "claude-sonnet-5-5-high", 5.10), (23, "composer-2.5", 0.40)]
TURNS = [("claude-sonnet-5-5-high", "a1", 180_000, 150_000, 6_000), ("claude-sonnet-5-5-high", "a1", 260_000, 240_000, 9_000),
         ("auto-smart", "b2", 120_000, 90_000, 4_000), ("composer-2.5", "c3", 300_000, 250_000, 12_000)]
ANSI = re.compile(r"\033\[([\d;]*)m")


def build(home: Path) -> None:
    (home / "config.json").write_text(json.dumps({"cycle_limit": 200, "model_budgets": {
        "claude-opus-*": 40, "claude-sonnet-*": 60, "auto*": 30, "gpt-*": 15}}), encoding="utf-8")
    rows = [{"Model": m, "Cost": f"{usd:.2f}", "Date": f"2026-10-{d:02d}T15:00:00.000Z"} for d, m, usd in DAYS]
    budget.seed(rows, home=home, now=lambda: NOW - 3600)
    for i, (model, conv, inp, cached, out) in enumerate(TURNS):
        budget.on_stop({"hook_event_name": "stop", "model": model, "conversation_id": conv, "input_tokens": inp,
                        "cache_read_tokens": cached, "output_tokens": out}, home=home, now=lambda: NOW - 1800 + i * 300)


def to_html(text: str) -> str:
    out, open_span = [], False
    for part in re.split(r"(\033\[[\d;]*m)", text):
        match = ANSI.fullmatch(part)
        if not match:
            out.append(html.escape(part))
            continue
        if open_span:
            out.append("</span>")
            open_span = False
        codes = match.group(1).split(";")
        if codes in (["0"], [""]):
            continue
        style = "font-weight:700;" if "1" in codes else ""
        if "38" in codes:
            i = codes.index("38")
            style += "color:rgb({},{},{});".format(*codes[i + 2:i + 5])
        out.append(f'<span style="{style}">')
        open_span = True
    return ("<!doctype html><meta charset=\"utf-8\"><title>cbudget report</title><body style=\"margin:0;"
            "background:#141414\"><pre style=\"margin:0;padding:24px;color:#d8d8d8;font:13px/1.35 Menlo,monospace\">"
            + "".join(out) + "</pre></body>")


def main(dest: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        build(home)
        report = render.report(7, home=home, color=True, now=NOW, columns=100)
    Path(dest).write_text(to_html(report), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "report.html")
