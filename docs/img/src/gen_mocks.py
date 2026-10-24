"""Write the HTML sources of the captures that need made-up data or an app screen.
  python3 docs/img/src/gen_mocks.py && python3 ../harness-docs/hdoc.py render
terminal-report, terminal-limites and terminal-editar are real output of the current cbudget, run against a throwaway
budget folder with an invented cycle (about $85 of $200), so no capture shows anybody's spend. terminal-editar feeds
`limits edit` scripted answers, shown after each question. The cursor-* pages are mocks of Cursor's chat, rebuilt from
real sessions of 2026-10-24 with the numbers swapped for the invented cycle's."""
from __future__ import annotations

import html
import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from cbudget import budget, limits, render  # noqa: E402

NOW = 1792850000.0  # 2026-10-24 10:53 -03, the tests' clock
# (day of October, model, usd): an invented cycle, about $84 before today
DAYS = [(2, "claude-sonnet-5-5-high", 3.10), (3, "auto", 2.40), (6, "claude-sonnet-5-5-high", 6.80),
        (7, "composer-2.5", 0.90), (8, "gpt-5.6-terra-medium", 1.70), (9, "claude-opus-5-5-high", 9.60),
        (10, "auto", 3.30), (13, "claude-sonnet-5-5-high", 5.20), (14, "auto", 4.10),
        (15, "claude-sonnet-5-5-high", 7.40), (16, "claude-opus-5-5-high", 12.80), (17, "auto", 2.20),
        (20, "claude-sonnet-5-5-high", 4.60), (21, "gpt-5.6-terra-medium", 2.30), (21, "claude-opus-5-5-high", 8.90),
        (22, "auto", 3.50), (23, "claude-sonnet-5-5-high", 5.10), (23, "composer-2.5", 0.40)]
TURNS = [("claude-sonnet-5-5-high", "a1", 180_000, 150_000, 6_000),
         ("claude-sonnet-5-5-high", "a1", 260_000, 240_000, 9_000),
         ("auto-smart", "b2", 120_000, 90_000, 4_000), ("composer-2.5", "c3", 300_000, 250_000, 12_000)]
# limits edit: Enter keeps a value; these change block_day, Opus's budget and add one for Composer
ANSWERS = ["", "", "", "12", "", "", "", "", "30", "", "", "", "composer-*", "10", "", "", "s"]
ANSI = re.compile(r"(\033\[[\d;]*m)")

TERM_STYLE = """*{box-sizing:border-box}body{margin:0;background:#cfd3e6;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",Helvetica,Arial,sans-serif}
.win{margin:22px;border-radius:12px;overflow:hidden;box-shadow:0 18px 50px rgba(20,20,40,.35),0 0 0 1px rgba(0,0,0,.25)}
.bar{height:38px;background:#2b2b31;display:flex;align-items:center;padding:0 14px;position:relative}
.dots span{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:8px}
.dots .r{background:#ff5f57}.dots .y{background:#febc2e}.dots .g{background:#28c840}
.ttl{position:absolute;left:0;right:0;text-align:center;color:#a9a9b3;font-size:13px}
.term{background:#16161a;color:#e6e6ea;font:13.5px/1.2 Menlo,Monaco,monospace;padding:14px 18px 18px;white-space:pre}
.p{color:#7ee787}.k{color:#79c0ff}.u{color:#ffa657}"""

CHAT_STYLE = """*{box-sizing:border-box}body{margin:0;background:#cfd3e6;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",Helvetica,Arial,sans-serif}
.win{margin:22px;width:1196px;border-radius:12px;overflow:hidden;box-shadow:0 18px 50px rgba(20,20,40,.35),0 0 0 1px rgba(0,0,0,.18)}
.bar{height:38px;background:#e4e4e6;display:flex;align-items:center;padding:0 14px;position:relative;border-bottom:1px solid #d6d6d9}
.dots span{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:8px}
.dots .r{background:#ff5f57}.dots .y{background:#febc2e}.dots .g{background:#28c840}
.ttl{position:absolute;left:0;right:0;text-align:center;color:#6b6b72;font-size:13px}
.chat{background:#f0f0f0;padding:22px 0 26px}.col{width:780px;margin:0 auto;color:#1d1d1f;font-size:15px;line-height:1.6}
.ask{background:#fff;border:1px solid #e2e2e5;border-radius:10px;padding:10px 14px;margin-bottom:16px}
.dim{color:#8a8a8f;margin:6px 0}.dim b{color:#1d1d1f;font-weight:400}
p{margin:10px 0}h3{font-size:19px;margin:22px 0 8px}ol{margin:6px 0;padding-left:24px}li{margin:6px 0}
code{font:13.5px Menlo,monospace;background:#e3e3e6;border-radius:4px;padding:1px 5px}
table{width:100%;border-collapse:separate;border-spacing:0;border:1px solid #dcdce0;border-radius:10px;overflow:hidden;background:#f7f7f8;margin:8px 0}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid #dcdce0}th{font-weight:600}tr:last-child td{border-bottom:0}
td+td,th+th{border-left:1px solid #dcdce0}
.foot{color:#a0a0a6;font-size:13px;margin-top:14px}
.composer{margin-top:18px;background:#fff;border:1px solid #e2e2e5;border-radius:12px;padding:12px 14px;color:#9a9aa0}
.blocked{margin-top:18px;border:1px solid #f0c2bd;background:#fdf1f0;border-radius:10px;padding:10px 14px;color:#9b2c22}
.blocked b{display:block;color:#7c1d15}"""


def build(home: Path) -> None:
    (home / "config.json").write_text(json.dumps({"cycle_limit": 200, "model_budgets": {
        "claude-opus-*": 40, "claude-sonnet-*": 60, "auto*": 30, "gpt-*": 15}}), encoding="utf-8")
    rows = [{"Model": m, "Cost": f"{usd:.2f}", "Date": f"2026-10-{d:02d}T15:00:00.000Z"} for d, m, usd in DAYS]
    budget.seed(rows, home=home, now=lambda: NOW - 3600)
    for i, (model, conv, inp, cached, out) in enumerate(TURNS):
        budget.on_stop({"hook_event_name": "stop", "model": model, "conversation_id": conv, "input_tokens": inp,
                        "cache_read_tokens": cached, "output_tokens": out}, home=home,
                       now=lambda i=i: NOW - 1800 + i * 300)


def ansi_html(text: str) -> str:
    out, open_span = [], False
    for part in ANSI.split(text):
        match = ANSI.fullmatch(part)
        if not match:
            out.append(html.escape(part))
            continue
        if open_span:
            out.append("</span>")
            open_span = False
        codes = part[2:-1].split(";")
        if codes in (["0"], [""]):
            continue
        style = "font-weight:700;" if codes[0] == "1" else ""
        if "38" in codes:
            i = codes.index("38")
            style += "color:rgb({},{},{});".format(*codes[i + 2:i + 5])
        out.append(f'<span style="{style}">')
        open_span = True
    return "".join(out)


def terminal(command: str, body: str, title: str = "zsh — ~") -> str:
    return (f'<!doctype html><meta charset="utf-8"><style>{TERM_STYLE}</style><div class="win" style="width:1196px">'
            '<div class="bar"><div class="dots"><span class="r"></span><span class="y"></span><span class="g"></span>'
            f'</div><div class="ttl">{html.escape(title)}</div></div><div class="term"><span class="p">$</span> '
            f'<span class="k">{html.escape(command)}</span>\n{body}</div></div>\n')


def edit_transcript(home: Path) -> str:
    answers, lines = iter(ANSWERS), [""]

    def ask(prompt: str) -> str:
        answer = next(answers)
        head, sep, last = prompt.rpartition("\n")
        lines[-1] += html.escape(head + sep + last) + f'<span class="u">{html.escape(answer)}</span>'
        lines.append("")
        return answer

    def say(text: str) -> None:
        lines[-1] += html.escape(text)
        lines.append("")

    limits.edit(home=home, ask=ask, say=say)
    return "\n".join(lines).rstrip("\n")


def chat(title: str, body: str) -> str:
    return (f'<!doctype html><meta charset="utf-8"><style>{CHAT_STYLE}</style><div class="win"><div class="bar">'
            '<div class="dots"><span class="r"></span><span class="y"></span><span class="g"></span></div>'
            f'<div class="ttl">{html.escape(title)}</div></div><div class="chat"><div class="col">{body}</div></div></div>\n')


GASTO = """<div class="ask">¿cuánto llevo gastado en Cursor y cómo están mis límites?</div>
<div class="dim">Worked for 11s</div>
<p><b>Ciclo:</b> $84.96 / $200 — te quedan <b>$115.04</b> y <b>8 días</b> (se reinicia el 1 de nov). Hoy: $0.66.</p>
<p><b>Por modelo (ciclo):</b></p>
<table><tr><th>Modelo</th><th>Uso</th></tr>
<tr><td>Opus</td><td>$31.30 / $40 (78%)</td></tr><tr><td>Sonnet</td><td>$32.60 / $60 (54%)</td></tr>
<tr><td>Auto</td><td>$15.65 / $30 (52%)</td></tr><tr><td>GPT</td><td>$4.00 / $15 (27%)</td></tr></table>
<p><b>Límites activos:</b> aviso chat $2 / bloqueo $5 · aviso día $8 / bloqueo $15 · Opus high/xhigh/max bloqueados.
Chequeo: sin problemas.</p>
<p>El total mezcla lo cobrado del CSV más lo estimado por los hooks desde entonces.</p>
<h3>Próximos pasos</h3>
<ol><li><b>Ojo con Opus</b> (78%): a este ritmo llega a su tope antes del cierre. Si lo necesitás, subilo:
<code>limits model 'claude-opus-*' 50</code>.</li>
<li>Ver qué chat se llevó el pico del 16 de oct: <code>report --color never</code>.</li></ol>
<div class="foot">1m ago</div>"""

TOPE = """<div class="ask">bajá el tope diario a 12</div>
<div class="dim">Worked for 17s ⌄</div>
<div class="dim">Thought briefly</div>
<p>Bajo <code>block_day</code> a $12 y verifico que quede coherente.</p>
<div class="dim"><b>Ran</b> Set block_day to 12 and check</div>
<div class="dim">Thought briefly</div>
<div class="dim"><b>Ran</b> Set block_day to 12 outside sandbox ›</div>
<div class="dim">Thought 1s</div>
<div class="dim"><b>Ran</b> Verify limits coherence after change</div>
<div class="dim">Thought briefly</div>
<p>Listo: <code><b>block_day</b></code> <b>= $12.00</b> (antes $15). Chequeo: ok.</p>
<div class="foot">1m ago</div>"""

BLOQUEO = """<div class="ask">revisá el módulo de pagos y proponé un refactor</div>
<div class="blocked"><b>Submission blocked by hook</b>cursor-budget: el modelo claude-opus-5-5-high está bloqueado;
elegí otro o corré `cbudget allow`</div>
<div class="composer">Plan, search, build anything… &nbsp;·&nbsp; Opus 5.5 High</div>"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        build(home)
        pages = {
            "terminal-report": terminal("cbudget report", ansi_html(render.report(7, home=home, color=True, now=NOW,
                                                                                  columns=100))),
            "terminal-limites": terminal("cbudget limits", ansi_html(limits.show(home=home, color=True, now=NOW))),
            "terminal-editar": terminal("cbudget limits edit", edit_transcript(home)),
        }
    pages["cursor-gasto"] = chat("Cursor — Chat", GASTO)
    pages["cursor-tope"] = chat("Cursor — Chat", TOPE)
    pages["cursor-bloqueo"] = chat("Cursor — Chat", BLOQUEO)
    for name, page in pages.items():
        (HERE / f"{name}.html").write_text(page, encoding="utf-8")
        print(f"docs/img/src/{name}.html")


if __name__ == "__main__":
    main()
