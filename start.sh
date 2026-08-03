#!/usr/bin/env bash
# Jude öffnen: läuft bereits eine Instanz, wird nur das Cockpit angezeigt,
# sonst startet das Desktop-Fenster mit Sprachsteuerung.
# Weitere Argumente werden an main.py durchgereicht, z.B.: ./start.sh --gui
set -e
cd "$(dirname "$0")"
PORT="${JUDE_PORT:-8765}"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r AGENT/requirements.txt
fi
# Bereits laufende Instanz? Dann nur das Cockpit im Browser öffnen.
if curl -s -o /dev/null -m 1 "http://127.0.0.1:${PORT}/"; then
  xdg-open "http://127.0.0.1:${PORT}/" >/dev/null 2>&1 || true
  exit 0
fi
cd AGENT
exec ../.venv/bin/python main.py --desktop --voice "$@"
