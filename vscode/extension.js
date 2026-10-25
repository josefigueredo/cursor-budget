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
const EVERY_MS = 10000;

function activate(context) {
  const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  item.name = "cursor-budget";
  item.command = "cursorBudget.report";
  item.show();
  let output = null;

  function refresh() {
    if (!fs.existsSync(ENTRY)) {
      item.text = "$(pulse) cursor-budget sin instalar";
      item.tooltip = "No encontré ~/.cursor/budget/app/cbudget.py: instalá cursor-budget.";
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
