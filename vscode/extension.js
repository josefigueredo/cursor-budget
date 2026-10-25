// cursor-budget in Cursor's status bar: today's and the cycle's spend, yellow at a warning and red at a block.
// It runs the installed CLI (`cbudget statusline --json`) with macOS's python, like the hooks do, every 10 seconds
// and right after a turn is recorded. A click opens the report. Without cursor-budget installed it says so.
"use strict";

const vscode = require("vscode");
const cp = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const {label} = require("./format");

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
  item.command = "cursorBudget.report";
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
        item.tooltip = "Gasto estimado de Cursor (cursor-budget). Clic: ver el reporte.";
        item.backgroundColor = l.level === "ok" ? undefined
          : new vscode.ThemeColor(l.level === "error" ? "statusBarItem.errorBackground" : "statusBarItem.warningBackground");
      } catch (e) {
        item.text = "$(pulse) cursor-budget";
        item.tooltip = "No pude leer el gasto: " + (e && e.message ? e.message : e);
        item.backgroundColor = undefined;
      }
    });
  }

  context.subscriptions.push(item, vscode.commands.registerCommand("cursorBudget.report", () => {
    if (!pythonReady()) return refresh();
    output = output || vscode.window.createOutputChannel("cursor-budget");
    cp.execFile(PYTHON, [ENTRY, "report", "--color", "never"], {timeout: 15000}, (err, stdout, stderr) => {
      output.clear();
      output.append(err ? "No pude correr el reporte: " + (stderr || err.message) : stdout);
      output.show(true);
    });
  }));

  refresh();
  const timer = setInterval(refresh, EVERY_MS);
  fs.watchFile(LEDGER, {interval: 2000}, refresh);
  context.subscriptions.push({dispose: () => { clearInterval(timer); fs.unwatchFile(LEDGER, refresh); }});
}

function deactivate() {}

module.exports = {activate, deactivate};
