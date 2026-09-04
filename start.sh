#!/bin/bash
cd /media/d4sd1ng/AI-Data/Projects/Jude/AGENT

# Start server in background
/media/d4sd1ng/AI-Data/Projects/Jude/.venv/bin/python main.py --gui --host 0.0.0.0 --port 8765 &
SERVER_PID=$!

# Wait for server to be ready
sleep 2

# Open browser
xdg-open http://127.0.0.1:8765

wait $SERVER_PID
