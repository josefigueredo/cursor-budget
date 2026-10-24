#!/usr/bin/env bash
# Rebuild every capture: the mock pages, the terminal transcripts against a throwaway HOME, then the PNGs.
#   docs/img/src/capturas.sh [path/to/hdoc.py]
# Needs harness-docs (hdoc) and Chrome. No notification fires and nothing outside the throwaway HOME changes.
set -euo pipefail
cd "$(dirname "$0")/../../.."
HDOC="${1:-$HOME/.agents/skills/harness-docs/hdoc.py}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
REAL="$(cd "$TMP" && pwd -P)"
mkdir -p "$TMP/bin"
printf '#!/bin/bash\nexec python3 "$HOME/.cursor/budget/app/cbudget.py" "$@"\n' > "$TMP/bin/cbudget"
chmod +x "$TMP/bin/cbudget"

python3 docs/img/src/gen_mocks.py
for name in instalar uso hook; do  # in this order: uso and hook use what instalar installed
  python3 "$HDOC" term "docs/img/src/terminal-$name.txt" --title "zsh — ~/cursor-budget" --cwd . \
    --env HOME="$TMP" --env SHELL=/bin/zsh --env CURSOR_BUDGET_NO_NOTIFY=1 --env PATH="$TMP/bin:$PATH" \
    --shorten "$REAL=~" --shorten "$TMP=~"
done
python3 "$HDOC" render
