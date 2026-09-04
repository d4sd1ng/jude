#!/usr/bin/env bash
# Jude beim Login im Hintergrund starten: Server + Sprachsteuerung, ohne Fenster.
# Das Cockpit lässt sich danach jederzeit über das Jude-Symbol öffnen.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r AGENT/requirements.txt
fi
cd AGENT
exec ../.venv/bin/python main.py --gui --host 0.0.0.0 --port "${JUDE_PORT:-8765}" --voice
