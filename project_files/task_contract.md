# Arbeitsvertrag (task_contract.md)

Wie an Jude gearbeitet wird — gilt für Menschen und KI-Agenten gleichermaßen.

## Grundregeln (von Tino gesetzt)

1. **Eine Sache auf einmal.** Fokus auf die bestellte Aufgabe; keine
   ungefragten Nebenbaustellen. Maße/Angaben wörtlich nehmen.
2. **Messwerte statt Behauptungen.** Jede Diagnose und jede Optimierung wird
   gemessen (Tokens, Sekunden, Cent, Logzeilen), nicht vermutet.
3. **Modelle werden niemals beschnitten.** Kontextlänge, max_tokens und
   Fähigkeits-Tags werden nicht zur Tempo-/Kostenoptimierung gekürzt.
   Tempo-Probleme löst man über Prompts, Caching, Infrastruktur oder
   Modell-WAHL — Optionen mit Trade-offs vorher vorlegen.
4. **Nur Bestelltes produzieren.** Das Team erzeugt genau die beauftragten
   Stücke (z. B. „nur der Montag-Launch-Post"), nie eigenmächtig den ganzen
   Kalender. Autopilot bleibt aus, bis Tino ihn ausdrücklich aktiviert.
5. **Nichts gilt als fertig, was nicht vorgelegt wurde.** Jedes Erzeugnis
   läuft durch submit_for_review → Chefprüfung → Abnahme durch Tino.
   Veröffentlichen/Versenden bleibt IMMER Tinos Handgriff.

## Arbeitsteilung

- **Tino** bestellt, gibt Richtung, nimmt ab, entscheidet über Geld, Rollen
  und Veröffentlichung.
- **Jude** führt das Team: erteilt Aufträge (Auftragsbuch), prüft Ergebnisse
  (Chefprüfung, max. 2 Revisionen, dann Vorbehalts-Vorlage an Tino),
  meldet Zustand (Briefing „Schreibtisch", GUI-Meldungen).
- **Mitarbeiter** arbeiten ausschließlich ihre Aufträge und Revisionen ab,
  durchgehend auf Deutsch, und legen alles vor. Texte kommen von Heinz.
- **Claude (Entwicklung)** plant und kontrolliert; mechanische Umsetzung
  delegiert er an Executor-Agenten. Executor-Agenten fassen NIEMALS Git an.

## Code-Regeln

- Pfade nur über `AGENT/core/paths.py`; keine absoluten Pfade in Modulen.
- Kommentare erklären das WARUM (gern mit Messwert), nicht das Was.
- Jede Änderung: `py_compile`-Check + gezielter Funktionstest vor dem Commit.
- Commits auf `feature/voice-dwd-radar-cockpit` (bzw. aktuellen Arbeitsbranch),
  deutsche Commit-Messages mit Begründung; Push nur auf Tinos Ansage.
- Datenordner (`data/`, `models/`, `austausch/` …) bleiben per .gitignore
  außerhalb des Repos.

## Entscheidungen & Dokumentation

- Jede nicht-triviale Entscheidung kommt mit Datum und Begründung in
  `project_files/decision_log.md`.
- Offenes landet in `project_files/todo.md` — oder als Auftrag im
  Auftragsbuch, wenn das Team es erledigen soll.
