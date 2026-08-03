#!/usr/bin/env bash
# Jude öffnen: startet den Server mit Sprachsteuerung (falls nicht schon aktiv)
# und zeigt das Cockpit in einem schlanken Browser-App-Fenster.
# Weitere Argumente werden an main.py durchgereicht.
set -e
cd "$(dirname "$0")"
PORT="${JUDE_PORT:-8765}"
URL="http://127.0.0.1:${PORT}/"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r AGENT/requirements.txt
fi

# Server starten, falls noch keiner läuft.
if ! curl -s -o /dev/null -m 1 "$URL"; then
  ( cd AGENT && exec ../.venv/bin/python main.py --gui --host 127.0.0.1 --port "$PORT" --voice "$@" ) &
  for _ in $(seq 1 60); do curl -s -o /dev/null -m 1 "$URL" && break; sleep 0.5; done
fi

# Cockpit als App-Fenster öffnen (wie eine native App); sonst normaler Tab.
for browser in brave-browser chromium chromium-browser google-chrome google-chrome-stable; do
  if command -v "$browser" >/dev/null 2>&1; then
    exec "$browser" --app="$URL" --class=Jude >/dev/null 2>&1
  fi
done
exec xdg-open "$URL"
