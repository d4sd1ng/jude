#!/usr/bin/env bash
# Jude unter Debian/Linux starten (Desktop-Fenster mit Sprachsteuerung).
# Weitere Argumente werden an main.py durchgereicht, z.B.: ./start.sh --gui
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r AGENT/requirements.txt
fi
cd AGENT
exec ../.venv/bin/python main.py --desktop --voice "$@"
