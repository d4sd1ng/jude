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
# Server bindet auf 0.0.0.0 (LAN-Zugriff), aber _origin_guard in web/app.py
# laesst standardmaessig nur Loopback-Hosts durch - zustandsaendernde Anfragen
# (POST/PUT/DELETE) von anderen Geraeten im Netzwerk wuerden sonst mit 403
# abgewiesen (11.09.2026, Copilot-PR-Review auf PR #10). Eigene LAN-IP daher
# automatisch in die Allowlist aufnehmen.
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -n "$LAN_IP" ]; then
  export JUDE_ALLOWED_HOSTS="${JUDE_ALLOWED_HOSTS:+$JUDE_ALLOWED_HOSTS,}$LAN_IP"
fi

cd AGENT
exec ../.venv/bin/python main.py --gui --host 0.0.0.0 --port "${JUDE_PORT:-8765}" --voice
