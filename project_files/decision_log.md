# Entscheidungs-Log

Neueste zuerst. Jede Zeile: Datum · Entscheidung · Begründung/Messwert.

## 2026-08-16

- **Workflow v2 eingeführt** (Commit aef3ad9): Auftragsbuch mit Lebenszyklus,
  Revisionsbremse (max. 2 Runden, dann Vorbehalts-Freigabe an Tino),
  Chefprüfung mit Auftragskontext + Faktenblock + Beanstandungs-Historie,
  stündlicher Auftragswächter, Briefing-Segment „Schreibtisch",
  GUI-Meldungen, Prompt-Diagnose bei 3× gleicher Lehre. Grund: 21 Läufe →
  1 Vorlage; Bestellungen versickerten; Prüfer erfand in jeder Runde neue
  Einwände.
- **Groq-Modell → `openai/gpt-oss-120b`**: Groq schaltete
  llama-3.3-70b-versatile zum 16.08. ab. Nachfolger per /models verifiziert,
  Deutsch + Prüfformat getestet. Schlüssel `cloud_groq_llama` bewusst
  behalten (Referenzen in sub_agents.json und TEXT_MODELL). Kein tools-Tag
  bis zur Neumessung.
- **Anthropic-Provider-Timeout 60 s → 180 s**: 60 s riss lange
  Content-Generierungen (Newsletter-Konzept) mitten im Lauf ab.
- **429-Providersperre** (2× 429 → 15 min überspringen): OpenAI stand einen
  Tag auf 429 und kostete in jeder Fallback-Kette einen vergeblichen Versuch.
- **Scheduler**: Fehler-Strings („… fehlgeschlagen:") zählen als Fehler
  (vorher Schein-Erfolg); lange Werkzeug-Aufgaben laufen im Daemon-Thread,
  damit der chat_lock frei bleibt.
- **Governance-Dokumente eingeführt** (project_files/): Overview, Contract,
  Decision-Log, Todo — analog zum Homepage-Repo.

## 2026-08-15

- **Team-Läufe → Claude Haiku 4.5**: qwen3:8b brauchte auf der RX 580
  (Modell nur zu 73 % in der GPU) 9+ Minuten bis Timeout pro Lauf. Haiku:
  77–129 s, 1,5–11 Cent/Lauf. Von Tino freigegeben (~10 $/Monat je Marke,
  ~20 $ mit Politara). Heinz bleibt auf Groq (kostenlos), Judes Chat lokal.
- **Prompt-Caching im AnthropicAdapter aktiviert** (`cache_control`
  ephemeral): Doku-lastige Läufe schleppten 78k Input-Tokens pro Runde voll
  bezahlt mit; mit Caching nur noch 1,5k voll bezahlte Tokens.
- **Groq-Kontingent resettet pro UTC-Tag** (kontingent.py): Messung vom
  Vortag (82 % verbraucht) bremste sonst den Folgetag aus.
- **Chefprüfung weicht bei knappem Groq-Kontingent direkt auf Haiku aus**:
  die allgemeine Kette begann lokal und kostete gemessen 300 s Timeout.
- **Kontext von qwen NICHT reduziert** (16384 bleibt): Tino-Regel „Modelle
  werden niemals beschnitten" — Tempo stattdessen über Cloud-Arbeitsmodell.
- **Datenbestand in den Projektordner umgezogen** (`JUDE_DIR` = Projektstamm):
  Tino will EINEN Jude-Ordner; alter Ort `/media/…/AI-Data/Jude/` aufgelöst.
- **Datei-Austausch etabliert** (`austausch/{an-team,vom-team,freigegeben}`)
  samt GUI-Upload „Dokument an Jude übergeben" und `dokument_zustellen`.
- **Wakeword-Erkennung repariert**: Callback-Ringpuffer statt blockierendem
  Lesen (11.000+ Lauschversuche ohne Treffer, Puffer-Überlauf zerriss die
  Phrase), fehlertoleranter Abgleich, Halluzinations-Filter im Log.
- **Team-Roster + Deutsch-Pflicht + Abnahme-Pflicht in die Prompts**:
  Modell erfand sonst Agentennamen, antwortete englisch und lieferte ohne
  Vorlage ab.
- **Frank (analyst, 31) angelegt**: Content-Scores Aufmerksamkeit/Conversion
  0–100; Clickbait senkt die Wertung (Nurovelle-Maßstab, bewusst gegen die
  Viral-Wortliste aus dem YouTube-Projekt).
- **Executor-Agenten dürfen kein Git**: Ein Agent hatte eigenmächtig
  committet und gepusht (32-MB-Datei); Historie per Force-Push bereinigt,
  Verbot in der Agenten-Definition verankert.

## Früher (Auszug)

- 2026-08-12: Team-Modellkette repariert; Werkzeuge der Mitarbeiter auf den
  Kern gekürzt. Messung: qwen ruft Werkzeuge zuverlässig, Groq-llama nicht.
- 2026-08-03: Modellsystem: dolphin3 unzensiert als Judes Standard, qwen3
  für Werkzeuge; Provider Groq/OpenRouter/DeepSeek über Schlüssel gated.
- 2026-08-02: Umzug von ~/Dokumente/Jarvis auf die NTFS-Datenplatte,
  Umbenennung Jarvis → Jude.
