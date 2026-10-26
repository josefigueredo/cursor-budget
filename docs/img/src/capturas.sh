#!/usr/bin/env bash
# Rebuild the captures: the mock pages, the terminal transcripts against a throwaway HOME, then the PNGs.
#   docs/img/src/capturas.sh [--hdoc path/to/hdoc.py] [name...]
# With names (terminal-report, cursor-panel-detalle…) only those PNGs, and those terminal transcripts, are rewritten.
# Needs harness-docs (hdoc) and Chrome. No notification fires and nothing outside the throwaway HOME changes.
# Deterministic on purpose, so a rerun touches only what changed: python3 reads a fixed clock (fixed_clock/), the time
# zone is the tests'. Two things still come from the machine: the commit in the install header (the version installed)
# and the Barra de Cursor row, which installs the extension only where Cursor is.
set -euo pipefail
cd "$(dirname "$0")/../../.."
HDOC="$HOME/.agents/skills/harness-docs/hdoc.py"
if [ "${1:-}" = "--hdoc" ]; then HDOC="$2"; shift 2; fi
NAMES=("$@")
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
REAL="$(cd "$TMP" && pwd -P)"
mkdir -p "$TMP/bin"
printf '#!/bin/bash\nexec python3 "$HOME/.cursor/budget/app/cbudget.py" "$@"\n' > "$TMP/bin/cbudget"
chmod +x "$TMP/bin/cbudget"
CLOCK="$PWD/docs/img/src/fixed_clock"  # only for the captured commands: hdoc itself keeps the real clock

wanted() {  # no names: everything
  [ "${#NAMES[@]}" -eq 0 ] && return 0
  local n
  for n in "${NAMES[@]}"; do [ "$n" = "$1" ] && return 0; done
  return 1
}

python3 docs/img/src/gen_mocks.py
for name in instalar uso hook; do  # in this order: uso and hook use what instalar installed, so instalar always runs
  out="docs/img/src/terminal-$name.html"
  if ! wanted "terminal-$name"; then
    [ "$name" = instalar ] || continue
    out="$TMP/discard.html"
  fi
  python3 "$HDOC" term "docs/img/src/terminal-$name.txt" -o "$out" --title "zsh — ~/cursor-budget" \
    --cwd . --env HOME="$TMP" --env SHELL=/bin/zsh --env CURSOR_BUDGET_NO_NOTIFY=1 --env PATH="$TMP/bin:$PATH" \
    --env TZ=America/Argentina/Buenos_Aires --env PYTHONPATH="$CLOCK" --shorten "$REAL=~" --shorten "$TMP=~"
done
python3 "$HDOC" render "${NAMES[@]+"${NAMES[@]}"}"
