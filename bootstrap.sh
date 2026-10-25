#!/usr/bin/env bash
# One-line install of cursor-budget, without cloning by hand:
#   gh api repos/josefigueredo/cursor-budget/contents/bootstrap.sh \
#     -H "Accept: application/vnd.github.raw" | bash
# The repo is internal, so plain curl gets a 404: gh brings the GitHub login. This clones the repo into a temporary
# folder, runs install.sh (which copies the program to ~/.cursor/budget/app) and deletes the clone.
# CURSOR_BUDGET_REPO overrides where to clone from (a path or URL), for testing.
set -euo pipefail

SLUG="josefigueredo/cursor-budget"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ -n "${CURSOR_BUDGET_REPO:-}" ]; then
  git clone --quiet --depth 1 "$CURSOR_BUDGET_REPO" "$TMP/repo"
elif command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  gh repo clone "$SLUG" "$TMP/repo" -- --quiet --depth 1
else
  echo "Hace falta gh con la sesión iniciada (brew install gh && gh auth login), o acceso SSH a GitHub." >&2
  git clone --quiet --depth 1 "git@github.com:$SLUG.git" "$TMP/repo"
fi

bash "$TMP/repo/install.sh"
echo "Listo. Abrí una terminal nueva y probá: cbudget report"
