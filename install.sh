#!/usr/bin/env bash
# Install cursor-budget into its own folder and wire it up.
#   ./install.sh
# Copies the program to ~/.cursor/budget/app (next to its ledger and config), so the hooks keep working if this
# repo moves or changes branch. Then points the Cursor hooks at that copy and adds `alias cbudget=...` to the
# shell rc. Re-running updates the copy; config.json, ledger.jsonl and baseline.json are never touched.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CURSOR_BUDGET_HOME:-$HOME/.cursor/budget}/app"
case "${1:-}" in
  -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
  "") ;;
  *) echo "unknown argument: $1" >&2; exit 2 ;;
esac

command -v python3 >/dev/null || { echo "python3 is not on PATH" >&2; exit 1; }
rm -rf "$DEST.new"
mkdir -p "$DEST.new"
cp "$REPO/cbudget.py" "$DEST.new/"
cp -R "$REPO/cbudget" "$DEST.new/"
find "$DEST.new" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "$DEST" && mv "$DEST.new" "$DEST"
echo "copiado en $DEST"

python3 "$DEST/cbudget.py" install-hook
python3 "$DEST/cbudget.py" alias
