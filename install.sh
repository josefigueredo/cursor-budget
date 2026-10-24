#!/usr/bin/env bash
# Install cursor-budget into its own folder and wire it up.
#   ./install.sh
# Copies the program and its skill to ~/.cursor/budget/app (next to its ledger and config), so the hooks keep
# working if this repo moves or changes branch. Points the Cursor hooks at that copy, adds `alias cbudget=...` to
# the shell rc, and exposes the skill: ~/.agents/skills/cursor-budget (Cursor) -> the copy, and
# ~/.claude/skills/cursor-budget and ~/.codex/skills/cursor-budget -> ../../.agents/skills/cursor-budget.
# Re-running updates the copy; config.json, ledger.jsonl, baseline.json and changes.jsonl are never touched.
# Ends with a test notification, since every warning arrives as one (CURSOR_BUDGET_NO_NOTIFY=1 skips it).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CURSOR_BUDGET_HOME:-$HOME/.cursor/budget}/app"
case "${1:-}" in
  -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
  "") ;;
  *) echo "unknown argument: $1" >&2; exit 2 ;;
esac

command -v python3 >/dev/null || { echo "python3 is not on PATH" >&2; exit 1; }
rm -rf "$DEST.new"
mkdir -p "$DEST.new"
cp "$REPO/cbudget.py" "$REPO/SKILL.md" "$DEST.new/"
cp -R "$REPO/cbudget" "$DEST.new/"
find "$DEST.new" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "$DEST" && mv "$DEST.new" "$DEST"
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
python3 "$DEST/cbudget.py" install-hook || status=1
python3 "$DEST/cbudget.py" alias || status=1
link "$DEST" "$HOME/.agents/skills/cursor-budget" || status=1
for host in .claude .codex; do
  link "../../.agents/skills/cursor-budget" "$HOME/$host/skills/cursor-budget" || status=1
done
echo "skill: cursor-budget (Cursor, Claude Code, Codex) -> $DEST"
# the warnings arrive as macOS notifications; one now shows whether they are allowed (CURSOR_BUDGET_NO_NOTIFY=1 skips it)
if [ "$(uname)" = Darwin ] && [ -z "${CURSOR_BUDGET_NO_NOTIFY:-}" ]; then
  python3 "$DEST/cbudget.py" notify-test || status=1
fi
exit "$status"
