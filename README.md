# Jude

Lokaler, deutschsprachiger KI-Assistent mit Web-GUI, Sprachsteuerung und
einem Team von Sub-Agenten, das Content für **Nurovelle** produziert.

## Start

```bash
./start.sh            # Debian: venv anlegen (falls nötig) + GUI + Sprache
# Windows: start.bat  |  Autostart: autostart.sh / Jude-Autostart.desktop
```

GUI: http://127.0.0.1:8765 · Wake-Word: „Jude angetreten" · Schlafwort:
„Jude Zapfenstreich".

## Dokumentation

| Dokument | Inhalt |
|---|---|
| [project_files/project_overview.md](project_files/project_overview.md) | Was Jude ist: Architektur, Team, Workflow, Marken |
| [project_files/task_contract.md](project_files/task_contract.md) | Wie gearbeitet wird (verbindliche Regeln) |
| [project_files/decision_log.md](project_files/decision_log.md) | Alle Entscheidungen mit Datum und Messwert |
| [project_files/todo.md](project_files/todo.md) | Offene Punkte |

## Aufbau in einem Absatz

`AGENT/main.py` baut den Agenten (Werkzeug-Registry, Modell-Router mit
lokalem Ollama + Cloud-Fallbacks, Team-Service) und startet die FastAPI-GUI
samt Sprach-Thread. Das Team (`data/sub_agents.json`) arbeitet Aufträge aus
dem **Auftragsbuch** ab (`services/auftraege.py`), legt Ergebnisse zur
**Chefprüfung** vor (`services/review.py`, max. 2 Revisionen) und Tino nimmt
in der GUI (System-Tab) oder per Chat ab. Der Datei-Austausch mit Tino läuft
über `austausch/` — Vorlagen und Briefings in `an-team/`, Ergebnisse in
`vom-team/`. Modelle und Preise: `AGENT/config/models.yaml`; Team-Läufe auf
Claude Haiku, Texte/Prüfung auf Groq (kostenlos), Judes Chat lokal.

## Konfiguration

`AGENT/.env` (siehe `.env.example`): API-Schlüssel (ANTHROPIC, GROQ, OPENAI,
NOTION …), `JUDE_WAKE_PHRASE`, `JUDE_BRIEFING`, `JUDE_PAID_MODELS_ENABLED`.
Pfade lösen sich über `AGENT/core/paths.py` auf — funktioniert unverändert
unter Debian und Windows (Dual-Boot, NTFS-Datenplatte).

## Tests

```bash
cd AGENT && ../.venv/bin/python -m pytest tests/ -x -q
```
