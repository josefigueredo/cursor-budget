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

# The output is a checklist: one row per part, ✓ done, · left as it was (optional), ✗ failed; then what to do next.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  G=$'\033[32m' Y=$'\033[33m' R=$'\033[31m' B=$'\033[1m' D=$'\033[2m' N=$'\033[0m'
else
  G="" Y="" R="" B="" D="" N=""
fi
status=0
TILDE='~'
short() { printf '%s' "${1//"$HOME"/$TILDE}"; }
row() { printf '  %s %-16s %s\n' "$1" "$2" "$(short "$3")"; }
ok() { row "${G}✓${N}" "$1" "$2"; }
skip() { row "${Y}·${N}" "$1" "$2"; }
fail() { row "${R}✗${N}" "$1" "$2"; status=1; }

before="$(head -c 7 "$DEST/VERSION" 2>/dev/null || true)"
now="$(git -C "$REPO" rev-parse --short=7 HEAD 2>/dev/null || true)"
if [ -z "$before" ] && [ ! -e "$DEST" ]; then what="instalación nueva"
elif [ -z "$before" ]; then what="actualización (la versión anterior no estaba registrada)"
elif [ "$before" = "$now" ]; then what="ya tenías esta versión: la reinstalé"
else what="actualización de $before a $now"; fi
echo
echo "${B}cursor-budget${N} ${now:-sin versión} ${D}· $what${N}"
echo

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
if [ -z "$before" ] && [ "$what" = "instalación nueva" ]; then ok "Programa" "en $DEST"
else ok "Programa" "en $DEST (tus límites, registro y seed quedan como estaban)"; fi

if out="$("$PY" "$DEST/cbudget.py" install-hook 2>&1)"; then
  case "$out" in *ya*) ok "Hooks" "stop y beforeSubmitPrompt, ya estaban en ~/.cursor/hooks.json" ;;
                 *) ok "Hooks" "stop y beforeSubmitPrompt sumados a ~/.cursor/hooks.json, sin tocar los demás" ;; esac
else fail "Hooks" "$out"; fi

if out="$("$PY" "$DEST/cbudget.py" alias 2>&1)"; then
  rc="${out##* en }"; rc="${rc%%;*}"
  case "$out" in
    "sin alias"*) skip "Alias" "tu shell no es zsh ni bash: cbudget alias te da la línea para pegar" ;;
    *ya*) ok "Alias" "cbudget, ya estaba en $rc" ;;
    *) ok "Alias" "cbudget agregado en $rc (abrí una terminal nueva)" ;;
  esac
else fail "Alias" "$out"; fi

link() {  # link <target> <path>: create or refresh a symlink, never replace a real file or directory
  if [ -e "$2" ] && [ ! -L "$2" ]; then
    echo "no reemplazo $2: no es un symlink; movelo y volvé a correr"
    return 1
  fi
  mkdir -p "$(dirname "$2")"
  ln -sfn "$1" "$2"
}
skill_err=""
skill_err+="$(link "$DEST" "$HOME/.agents/skills/cursor-budget")"
for host in .claude .codex; do
  skill_err+="$(link "../../.agents/skills/cursor-budget" "$HOME/$host/skills/cursor-budget")"
done
if [ -z "$skill_err" ]; then ok "Skill" "para Cursor, Claude Code y Codex"; else fail "Skill" "$skill_err"; fi

# cursor-agent's footer: optional, so a status line of the user's own is a note, not a failed install
if [ -f "$HOME/.cursor/cli-config.json" ]; then
  if out="$("$PY" "$DEST/cbudget.py" statusline install 2>&1)"; then
    case "$out" in *ya*) ok "cursor-agent" "el gasto en el pie, ya estaba" ;;
                   *) ok "cursor-agent" "el gasto en el pie (cerrá y volvé a abrir cursor-agent)" ;; esac
  else skip "cursor-agent" "tenías tu propio pie: lo dejé como estaba"; fi
else
  skip "cursor-agent" "no lo usás en esta máquina"
fi

# Cursor's status bar: optional too, since an organization may allow only approved extensions
# (CURSOR_BUDGET_NO_EXTENSION=1 skips it, for tests and captures)
# the app's own CLI first: a `cursor` on PATH can be a shim that re-execs itself when HOME is not the usual one
CURSOR_CLI="/Applications/Cursor.app/Contents/Resources/app/bin/cursor"
[ -x "$CURSOR_CLI" ] || CURSOR_CLI="$(command -v cursor || true)"
extension=""
if [ -n "${CURSOR_BUDGET_NO_EXTENSION:-}" ]; then
  skip "Barra de Cursor" "omitida (CURSOR_BUDGET_NO_EXTENSION)"
elif [ ! -x "$CURSOR_CLI" ]; then
  skip "Barra de Cursor" "no encontré Cursor instalado"
elif VSIX="$("$PY" "$REPO/vscode/build_vsix.py" "$DEST/vscode")" \
    && "$CURSOR_CLI" --install-extension "$VSIX" --force >/dev/null 2>&1; then
  ok "Barra de Cursor" "extensión con el gasto en la barra del editor"
  extension=yes
else
  skip "Barra de Cursor" "no pude instalar la extensión: Extensions: Install from VSIX… con ${VSIX:-vscode/}"
fi

# the warnings arrive as desktop notifications (osascript on macOS, notify-send on Linux); one now shows whether they
# are allowed (CURSOR_BUDGET_NO_NOTIFY=1 skips it)
notified=""
os=$(uname)
if [ "$os" = Linux ] && ! command -v notify-send >/dev/null && [ ! -x /usr/bin/notify-send ]; then
  skip "Avisos" "sin notify-send no hay avisos: instalá libnotify-bin (Debian, Ubuntu) o libnotify (Fedora, Arch)"
elif { [ "$os" = Darwin ] || [ "$os" = Linux ]; } && [ -z "${CURSOR_BUDGET_NO_NOTIFY:-}" ]; then
  if "$PY" "$DEST/cbudget.py" notify-test >/dev/null 2>&1; then
    ok "Avisos" "te mandé una notificación de prueba"
    notified=yes
  else fail "Avisos" "no pude mandar la notificación de prueba: cbudget notify-test"; fi
fi

echo
if [ "$status" = 0 ]; then echo "${B}Listo.${N} Qué sigue:"; else echo "${R}${B}Algo falló${N} (las filas con ✗). Lo demás quedó instalado. Qué sigue:"; fi
n=1
step() { echo "  $n. $1"; n=$((n + 1)); }
step "Abrí una terminal nueva y probá: ${B}cbudget report${N}"
[ -n "$extension" ] && step "En Cursor: Cmd+Shift+P → ${B}Developer: Reload Window${N}. El gasto queda abajo a la derecha del editor (botón IDE ↗)."
[ -n "$notified" ] && [ "$os" = Darwin ] && step "¿No viste la notificación? Configuración del Sistema → Notificaciones → Script Editor → Permitir notificaciones."
[ -n "$notified" ] && [ "$os" = Linux ] && step "¿No viste la notificación? Mirá ~/.cursor/budget/notify-errors.log y probá con: cbudget notify-test"
[ "$what" = "instalación nueva" ] && step "Para cargar lo que ya gastaste este mes, pedíselo al agente: «cargá mi CSV de uso de Cursor»."
exit "$status"
