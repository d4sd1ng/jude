@echo off
rem Jude unter Windows starten (Desktop-Fenster mit Sprachsteuerung).
rem Weitere Argumente werden an main.py durchgereicht, z.B.: start.bat --gui
cd /d "%~dp0"
if not exist .venv-win\Scripts\python.exe (
  py -3 -m venv .venv-win
  .venv-win\Scripts\python -m pip install --upgrade pip
  .venv-win\Scripts\pip install -r AGENT\requirements.txt
)
cd AGENT
..\.venv-win\Scripts\python main.py --desktop --voice %*
