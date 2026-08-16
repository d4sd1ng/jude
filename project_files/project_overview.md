# Projekt-Überblick: Jude

Stand: 16.08.2026

## Was Jude ist

Jude ist Tinos lokaler, deutschsprachiger KI-Assistent mit Web-GUI (FastAPI,
Port 8765), Sprachsteuerung (Wake-Word „Jude angetreten" / Schlafwort „Jude
Zapfenstreich", Whisper-Erkennung + Piper-Stimme) und einem Team benannter
Sub-Agenten („Mitarbeiter"), das Content und Zuarbeit für die Marke
**Nurovelle** produziert (später zusätzlich Politara).

## Verzeichnisse (alles in EINEM Projektordner)

| Pfad | Inhalt |
|---|---|
| `AGENT/` | Kompletter Code (main.py, core/, services/, speech/, tools/, web/) |
| `data/` | Laufzeitdaten: jude.db (SQLite), logs/, sub_agents.json, Kontingent |
| `models/`, `images/`, `backups/`, `tmp/` | Modelle, Bilder, Sicherungen, Werkstatt |
| `austausch/` | Datei-Austausch Tino ↔ Team: `an-team/` (inkl. `vorlagen/`), `vom-team/`, `freigegeben/` |
| `project_files/` | Diese Governance-Dokumente |

Pfadauflösung zentral in `AGENT/core/paths.py` (`JUDE_DIR` = Projektstamm,
läuft unter Debian und Windows). Niemals absolute Pfade in Code außerhalb
dieser Datei.

## Das Team (sub_agents.json)

12 Mitarbeiter mit Person, Rolle, Skills und Modell — u. a. Joana (engineer),
Bernd (outreach), Rasmus (scraper), Heike (designer, Bildgenerierung),
Tom (sequencer), Frieda (leadmanager), Stefan (content), Mike (social),
**Heinz (redakteur — schreibt alle Texte, läuft über Groq)**, Klaus
(beobachter), Renate (projektleitung), **Frank (analyst — Content-Scores:
Aufmerksamkeit/Conversion 0–100)**.

Team-Arbeitsmodell: **Claude Haiku 4.5** (werkzeugfähig, Sekunden statt
Minuten, ~1,5–11 Cent/Lauf). Heinz/Chefprüfung: **Groq gpt-oss-120b**
(kostenlos). Judes eigener Chat: lokales dolphin3; qwen3 lokal als Fallback.

## Workflow (v2, seit 16.08.)

```
Tino/Jude: auftrag_erteilen ──▶ Auftragsbuch (offen → in_arbeit)
   Mitarbeiter-Lauf (Haiku) ──▶ submit_for_review(auftrag_id) → vorgelegt
   Chefprüfung (Jude, max 2 Revisionen, kennt Auftrag+Fakten+Verlauf)
      ├─ FREIGABE ────────────▶ Abnahme bei Tino (GUI System-Tab / Chat)
      ├─ REVISION ────────────▶ zurück an Mitarbeiter (Historie in `verlauf`)
      └─ ab Runde 3 ──────────▶ Freigabe „mit Vorbehalt" – Tino entscheidet
   Auftragswächter (stündlich) fasst Überfälliges nach
   Briefing/GUI melden: „X Vorlagen zur Abnahme, Y Aufträge überfällig"
```

3× dieselbe Beanstandung → automatischer Auftrag „Prompt-Diagnose" an die
Projektleitung; Rollen ändert ausschließlich Tino (`rolle_aktualisieren`
mit Bestätigungs-Gate).

## Marken & Content

- **Nurovelle** — KI-Systeme für den Mittelstand. Einstieg immer die
  kostenlose KI-Potenzialanalyse (nurovelle.de/analyse.html). Stil: mattes
  Schwarz, Gold + Dunkelgrün, „teure Manufaktur", kein Werbedeutsch.
- Strategie/Kalender/Monetarisierung: `austausch/an-team/*.md` (4-Wochen-
  Wellen-Modell, 15 Slots/Woche; ab Woche 5 zusätzlich 4 Shorts + 2 Longs).
- Design-Referenzen: `austausch/an-team/vorlagen/` + Stilbeschreibung
  (Cradermind/Nexora sind rein visuelle Vorlagen — Themen nie übernehmen).

## Externe Abhängigkeiten

Anthropic (Team-Läufe, Caching aktiv), Groq (Heinz/Prüfung, Tageskontingent
mit UTC-Reset), OpenAI (Bilder; Responses-API; zeitweise 429), Ollama lokal
(dolphin3, qwen3, whisper, nomic-embed), Notion (Leads/Content-Ablage),
DWD/RainViewer (Radar), MT5 (Trading-Demo).
