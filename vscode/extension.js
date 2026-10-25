// cursor-budget in Cursor's status bar: today's and the cycle's spend, yellow at a warning and red at a block.
// It runs the installed CLI (`cbudget statusline --json`) with macOS's python, like the hooks do, every 10 seconds
// and right after a turn is recorded. A click opens a panel with two tabs, Detalle and Límites (panel.js, fed by
// `cbudget panel`); a change made there runs `cbudget limits … --by "extensión de Cursor"`, which validates it, logs it
// and notifies, like a change from the chat. Without cursor-budget installed it says so.
"use strict";

const vscode = require("vscode");
const cp = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const {label} = require("./format");
const {html} = require("./panel");
const crypto = require("crypto");

const HOME = path.join(os.homedir(), ".cursor", "budget");
const ENTRY = path.join(HOME, "app", "cbudget.py");
const LEDGER = path.join(HOME, "ledger.jsonl");
const PYTHON = "/usr/bin/python3";
// Without the Command Line Tools (often after a macOS upgrade) /usr/bin/python3 is a stub that opens an install
// dialog on every call: the extension checks for the real one first and never calls the stub.
const REAL_PYTHON = "/Library/Developer/CommandLineTools/usr/bin/python3";

let pythonFound = false;  // once found it stays; a missing one is looked for again on each refresh

function pythonReady() {
  if (pythonFound || fs.existsSync(REAL_PYTHON)) return (pythonFound = true);
  try {
    const dev = cp.execFileSync("/usr/bin/xcode-select", ["-p"], {timeout: 2000}).toString().trim();
    pythonFound = fs.existsSync(path.join(dev, "usr", "bin", "python3"));
  } catch (e) {
    pythonFound = false;
  }
  return pythonFound;
}
const EVERY_MS = 10000;

function activate(context) {
  const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  item.name = "cursor-budget";
  item.command = "cursorBudget.open";
  item.show();
  let output = null;

  function refresh() {
    if (!fs.existsSync(ENTRY) || !pythonReady()) {
      item.text = "$(pulse) cursor-budget sin instalar";
      item.tooltip = fs.existsSync(ENTRY)
        ? "Falta el python3 de macOS: corré xcode-select --install en una terminal."
        : "No encontré ~/.cursor/budget/app/cbudget.py: instalá cursor-budget.";
      item.backgroundColor = undefined;
      return;
    }
    cp.execFile(PYTHON, [ENTRY, "statusline", "--json"], {timeout: 5000}, (err, stdout) => {
      try {
        if (err) throw err;
        const s = JSON.parse(stdout);
        const l = label(s);
        item.text = l.text;
        item.tooltip = "Gasto estimado de Cursor (cursor-budget). Clic: el detalle y los límites.";
        item.backgroundColor = l.level === "ok" ? undefined
          : new vscode.ThemeColor(l.level === "error" ? "statusBarItem.errorBackground" : "statusBarItem.warningBackground");
      } catch (e) {
        item.text = "$(pulse) cursor-budget";
        item.tooltip = "No pude leer el gasto: " + (e && e.message ? e.message : e);
        item.backgroundColor = undefined;
      }
    });
  }

  let panel = null, tab = "detalle";
  const ACTOR = "extensión de Cursor";

  function cli(args, done) {
    cp.execFile(PYTHON, [ENTRY, ...args], {timeout: 15000}, (err, stdout, stderr) =>
      done(err ? String(stderr || err.message).trim() : "", stdout));
  }
  function render(error) {
    if (!panel) return;
    cli(["panel"], (err, stdout) => {
      if (!panel) return;
      try {
        if (err) throw new Error(err);
        panel.webview.html = html(JSON.parse(stdout), {nonce: crypto.randomBytes(16).toString("hex"),
          csp: panel.webview.cspSource, tab: tab, error: error || ""});
      } catch (e) {
        panel.webview.html = "<p>No pude leer el gasto: " + String(e.message || e).replace(/[<&]/g, "") + "</p>";
      }
    });
  }
  // a change from the panel: the CLI validates, logs and notifies; then the panel and the bar show the new state
  function change(args) {
    cli(["limits", "--by", ACTOR, ...args], (err) => { render(err); refresh(); });
  }
  function onMessage(m) {
    if (!m || typeof m !== "object") return;
    if (m.type === "tab") tab = m.tab === "limites" ? "limites" : "detalle";
    else if (m.type === "refresh") render();
    else if (m.type === "set") change(["set", String(m.key), String(m.value)]);
    else if (m.type === "block") change(["block", String(m.pattern)]);
    else if (m.type === "unblock") change(["unblock", String(m.pattern)]);
    else if (m.type === "model") change(["model", String(m.pattern), String(m.usd)]);
    else if (m.type === "model-remove") change(["model", String(m.pattern), "--remove"]);
    else if (m.type === "report") {
      output = output || vscode.window.createOutputChannel("cursor-budget");
      cli(["report", "--color", "never"], (err, stdout) => {
        output.clear();
        output.append(err ? "No pude correr el reporte: " + err : stdout);
        output.show(true);
      });
    }
  }

  context.subscriptions.push(item, vscode.commands.registerCommand("cursorBudget.open", () => {
    if (!fs.existsSync(ENTRY) || !pythonReady()) return refresh();
    if (panel) { panel.reveal(); render(); return; }
    panel = vscode.window.createWebviewPanel("cursorBudget", "cursor-budget", vscode.ViewColumn.Active,
                                             {enableScripts: true, localResourceRoots: []});
    panel.onDidDispose(() => { panel = null; }, null, context.subscriptions);
    panel.webview.onDidReceiveMessage(onMessage, null, context.subscriptions);
    render();
  }));

  refresh();
  const timer = setInterval(refresh, EVERY_MS);
  fs.watchFile(LEDGER, {interval: 2000}, refresh);
  context.subscriptions.push({dispose: () => { clearInterval(timer); fs.unwatchFile(LEDGER, refresh); }});
}

function deactivate() {}

module.exports = {activate, deactivate};
