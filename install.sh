#!/usr/bin/env bash
# Install cursor-budget into its own folder and wire it up.
#   ./install.sh
# Copies the program and its skill to ~/.cursor/budget/app (next to its ledger and config), so the hooks keep
# working if this repo moves or changes branch. Points the Cursor hooks at that copy, adds `alias cbudget=...` to
# the shell rc, and exposes the skill: ~/.agents/skills/cursor-budget (Cursor) -> the copy, and
# ~/.claude/skills/cursor-budget and ~/.codex/skills/cursor-budget -> ../../.agents/skills/cursor-budget.
# Re-running updates the copy; config.json, ledger.jsonl, baseline.json and changes.jsonl are never touched.
# The hooks and the alias run with macOS's /usr/bin/python3, which outlives Homebrew upgrades. The installed commit goes
# to app/VERSION, so a daily check can say when a newer one exists. cursor-agent's footer shows the spend
# (statusLine in ~/.cursor/cli-config.json), unless it already has a status line of its own, and Cursor's app shows it
# in the status bar (the extension in vscode/, built here and installed with `cursor --install-extension`).
# Ends with a test notification, since every warning arrives as one (CURSOR_BUDGET_NO_NOTIFY=1 skips it).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CURSOR_BUDGET_HOME:-$HOME/.cursor/budget}/app"
case "${1:-}" in
  -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
  "") ;;
  *) echo "unknown argument: $1" >&2; exit 2 ;;
esac

# Without the Command Line Tools, /usr/bin/python3 is a stub that opens a dialog on every hook: refuse instead.
/usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null || {
  echo "Falta el python3 de macOS: instalá las Command Line Tools con xcode-select --install y volvé a correr" >&2
  exit 1
}
PY=/usr/bin/python3
rm -rf "$DEST.new"
mkdir -p "$DEST.new"
cp "$REPO/cbudget.py" "$REPO/SKILL.md" "$DEST.new/"
cp -R "$REPO/cbudget" "$DEST.new/"
find "$DEST.new" -name __pycache__ -type d -prune -exec rm -rf {} +
git -C "$REPO" rev-parse HEAD > "$DEST.new/VERSION" 2>/dev/null || rm -f "$DEST.new/VERSION"
# swap by renames, so a hook that fires meanwhile finds the old copy or the new one, never nothing
rm -rf "$DEST.old"
[ -e "$DEST" ] && mv "$DEST" "$DEST.old"
mv "$DEST.new" "$DEST"
rm -rf "$DEST.old"
echo "copiado en $DEST"

link() {  # link <target> <path>: create or refresh a symlink, never replace a real file or directory
  if [ -e "$2" ] && [ ! -L "$2" ]; then
    echo "no reemplazo $2: no es un symlink; movelo y volvé a correr" >&2
    return 1
  fi
  mkdir -p "$(dirname "$2")"
  ln -sfn "$1" "$2"
}

status=0
"$PY" "$DEST/cbudget.py" install-hook || status=1
"$PY" "$DEST/cbudget.py" alias || status=1
# cursor-agent's footer: optional, so a status line of the user's own is a note, not a failed install
if [ -f "$HOME/.cursor/cli-config.json" ]; then
  "$PY" "$DEST/cbudget.py" statusline install || echo "(la línea de cursor-agent quedó como estaba)"
fi
link "$DEST" "$HOME/.agents/skills/cursor-budget" || status=1
for host in .claude .codex; do
  link "../../.agents/skills/cursor-budget" "$HOME/$host/skills/cursor-budget" || status=1
done
echo "skill: cursor-budget (Cursor, Claude Code, Codex) -> $DEST"
# Cursor's status bar: optional too, since an organization may allow only approved extensions
# (CURSOR_BUDGET_NO_EXTENSION=1 skips it, for tests and captures)
CURSOR_CLI="$(command -v cursor || true)"
[ -n "$CURSOR_CLI" ] || CURSOR_CLI="/Applications/Cursor.app/Contents/Resources/app/bin/cursor"
if [ -z "${CURSOR_BUDGET_NO_EXTENSION:-}" ] && [ -x "$CURSOR_CLI" ]; then
  if VSIX="$("$PY" "$REPO/vscode/build_vsix.py" "$DEST/vscode")" \
      && "$CURSOR_CLI" --install-extension "$VSIX" --force >/dev/null 2>&1; then
    echo "extensión de Cursor instalada: el gasto aparece en la barra de estado (si no, Developer: Reload Window)"
  else
    echo "(no pude instalar la extensión de Cursor; podés hacerlo a mano con Extensions: Install from VSIX… y ${VSIX:-vscode/})"
  fi
fi
# the warnings arrive as macOS notifications; one now shows whether they are allowed (CURSOR_BUDGET_NO_NOTIFY=1 skips it)
if [ "$(uname)" = Darwin ] && [ -z "${CURSOR_BUDGET_NO_NOTIFY:-}" ]; then
  "$PY" "$DEST/cbudget.py" notify-test || status=1
fi
exit "$status"
