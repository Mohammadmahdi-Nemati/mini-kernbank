#!/bin/bash
# Richtet den Projekt-Agenten ein (macOS/Linux). Kann gefahrlos mehrfach ausgeführt werden.
# Ohne Doppelklick:  bash agent/einrichten.command
cd "$(dirname "$0")" || exit 1

ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
info() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ende() { [ -t 0 ] && { echo; read -r -n1 -p "Taste drücken zum Schließen ..."; echo; }; exit "$1"; }
fail() { printf '  \033[31m✘ %s\033[0m\n' "$*"; ende 1; }

info "1/4 Python suchen"
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  p=$(command -v "$c" 2>/dev/null) || continue
  "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null && { PY="$p"; break; }
done
[ -z "$PY" ] && [ -x "$HOME/.local/bin/python3.11" ] && PY="$HOME/.local/bin/python3.11"
[ -n "$PY" ] || fail "Python >= 3.10 nicht gefunden"
ok "$PY ($("$PY" --version))"

info "2/4 Pakete installieren (beim ersten Mal einige Minuten)"
[ -x .venv/bin/python ] || "$PY" -m venv .venv || fail "venv anlegen"
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt || fail "pip install"
ok "Pakete in agent/.venv"

info "3/4 Schlüssel"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "  agent/.env angelegt. Trage dort deinen Schlüssel ein (kostenlos: https://build.nvidia.com)."
  if [ -t 0 ] && command -v open >/dev/null; then
    open -e .env; read -r -p "  Enter drücken, wenn gespeichert ... "
  fi
fi
if grep -Eq '^(NVIDIA|ANTHROPIC|OPENAI|GROQ)_API_KEY=.*x{8}' .env && ! grep -q '^LLM_ANBIETER=ollama' .env; then
  echo "  Hinweis: In agent/.env steht noch ein Platzhalter. Der Chat startet, antwortet aber erst mit echtem Schlüssel."
else
  ok ".env vorhanden"
fi

info "4/4 Wissensbasis bauen (lädt einmalig das Embedding-Modell, ~2,3 GB)"
.venv/bin/python app.py --nur-index || fail "Wissensbasis"
ok "Fertig. Starten mit Doppelklick auf agent/start.command"
ende 0
