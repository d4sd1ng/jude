# Offene Punkte (todo.md)

Stand: 16.08.2026. Erledigtes wird gestrichen und wandert ins decision_log.

## Kurzfristig (vor/zum Launch Montag 17.08.)

- [ ] **Montag-Launch-Post finalisieren** (Mike): Tinos Richtung „Launch,
  nicht Erklärstück" einarbeiten — Auftrag über das Auftragsbuch, dann
  Abnahme durch Tino. (Vorlage 8b0ea661b808, Runde 2.)
- [ ] **Banner-Bilder erzeugen**, sobald OpenAI-Bildgenerierung wieder
  Kapazität hat (429): Heikes freigegebene Spezifikationen (Vorlage
  a4433f4f84f4) in echte Bilder umsetzen.
- [ ] **Profiltexte abnehmen** (liegen bei Tino: profiltexte-social-media.md).
- [ ] **Newsletter-Konzept + Landingpage-Vorlagen abnehmen** (erste Aufträge
  durch das neue Auftragsbuch, Stefan/Joana).

## Nach dem Montag-Test

- [ ] **Kalender-Autopilot aktivieren** (nur auf Tinos Ansage „Autopilot an"):
  tägliche Aufgabe erzeugt fällige Kalender-Slots mit 2 Tagen Vorlauf als
  Aufträge.
- [ ] **Werkzeug-Verhalten von gpt-oss-120b auf Groq messen** — erst danach
  ggf. tools-Tag vergeben.
- [ ] **Prompt-Caching-Wirkung nachmessen** (cache_read-Anteil in
  model_usage über mehrere Team-Läufe).
- [ ] **Newsletter-Slot in den Kalender einhängen** (nach Abnahme des
  Konzepts), inkl. wöchentlicher Zweitverwertung der Post-Themen.

## Mittelfristig

- [ ] **Video-Pipeline anbinden**: ffmpeg/MoviePy-Strecke aus
  `Projects/youtube_automations` an Jude koppeln (Skript → Piper-Voiceover →
  Grafiken → Render). Stufe A der Kostenrechnung (~25 Cent/Short).
- [ ] **Politara-Kanal aufsetzen** (verdoppelt Content-Pensum, ~20 $/Monat
  gesamt). „Politara" bis dahin nirgends in Inhalten erwähnen.
- [ ] **Newsletter-Versand-Infrastruktur** wählen und anbinden (bewusst nicht
  Teil von Workflow v2 — bisher nur Inhalte/Sequenzen).
- [ ] **GUI-Formular für geplante Aufgaben** um `tool`/`tool_args` erweitern
  (heute nur per API/Werkzeug erreichbar).
- [ ] **tmp/ Rest aufräumen** (Wakeword-Trainingsdaten ~600 MB) und
  Entscheidung zur untracked 32-MB-Datei `pathlib` im Projektstamm.

## Bekannte Ärgernisse

- NewsAPI-Schlüssel abgelaufen (401), yfinance-Golddaten unzuverlässig,
  MT5-Login schlägt fehl („Authorization failed"), DWD-Downloads brechen
  sporadisch ab (SSL). Alles nicht launch-kritisch.
