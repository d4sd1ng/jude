#!/bin/bash
cd "$(dirname "$0")/AGENT"

# Server bindet auf 0.0.0.0 (LAN-Zugriff), aber _origin_guard in web/app.py
# laesst standardmaessig nur Loopback-Hosts durch - zustandsaendernde Anfragen
# (POST/PUT/DELETE) von anderen Geraeten im Netzwerk wuerden sonst mit 403
# abgewiesen (11.09.2026, Copilot-PR-Review auf PR #10). Eigene LAN-IP daher
# automatisch in die Allowlist aufnehmen.
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -n "$LAN_IP" ]; then
  export JUDE_ALLOWED_HOSTS="${JUDE_ALLOWED_HOSTS:+$JUDE_ALLOWED_HOSTS,}$LAN_IP"
fi

# Start server in background
../.venv/bin/python main.py --gui --host 0.0.0.0 --port 8765 &
SERVER_PID=$!

# Wait for server to be ready
sleep 2

# Open browser
xdg-open http://127.0.0.1:8765

wait $SERVER_PID
