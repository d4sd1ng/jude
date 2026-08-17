# Jude — Technische Dokumentation

Stand: 13.08.2026. Grundlage ist ausschließlich der Quellcode unter
`/media/d4sd1ng/AI-Data/Projects/Jude/` (Zweig `feature/voice-dwd-radar-cockpit`,
letzter Commit `5c47cd9`) sowie der Datenbestand unter
`/media/d4sd1ng/AI-Data/Jude/`.

Alle Pfad- und Zeilenangaben sind geprüft. Wo etwas nicht belegbar war, steht
das ausdrücklich dabei. Es sind keine Schlüssel oder Passwörter enthalten —
nur Variablennamen.

---

## Inhaltsverzeichnis

1. [Überblick und Start](#1-überblick-und-start)
2. [Modell-Routing](#2-modell-routing)
3. [Werkzeuge](#3-werkzeuge)
4. [Das Team](#4-das-team)
5. [Abnahme und Freigabe](#5-abnahme-und-freigabe)
6. [Notion](#6-notion)
7. [Weitere Dienste](#7-weitere-dienste)
8. [Web und GUI](#8-web-und-gui)
9. [Konfiguration](#9-konfiguration)
10. [Betrieb](#10-betrieb)
11. [Bekannte Einschränkungen](#11-bekannte-einschränkungen)
12. [Auffälligkeiten](#12-auffälligkeiten)

---

## 1. Überblick und Start

### 1.1 Was Jude ist

Jude ist ein lokal laufender Assistent: ein Chat-Agent mit Werkzeugen, einer
Web-Oberfläche, Sprachsteuerung und einem Team benannter Sub-Agenten. Der
Standardbetrieb ist lokal — die Modelle laufen über Ollama auf demselben
Rechner. Cloud-Modelle sind eine Eskalationsstufe, keine Grundlage.

Die drei tragenden Bausteine:

| Baustein | Datei | Aufgabe |
|---|---|---|
| `Agent` | `AGENT/core/agent.py:16` | Gesprächsverlauf, Systemprompt, Werkzeugschleife |
| `ModelRouter` | `AGENT/core/model_router.py:362` | Modellauswahl, Fallback-Kette, Kostenbuchhaltung |
| `ToolRegistry` | `AGENT/core/tool_registry.py:34` | Werkzeugverzeichnis, Bestätigungs-Gate |

### 1.2 Start

Einstiegspunkt ist `AGENT/main.py`. Argumente (`parse_args`, `main.py:73–88`):

| Argument | Vorgabe | Wirkung |
|---|---|---|
| `--once TEXT` | – | Eine Eingabe verarbeiten, dann beenden |
| `--voice` | aus | Mikrofon statt Texteingabe; im GUI-Betrieb setzt es `JUDE_VOICE=1` (`main.py:137–138`) |
| `--record-seconds` | 12.0 | Maximale Befehlsdauer nach dem Aktivierungswort |
| `--wake-word` | `JUDE_WAKE_PHRASE`, sonst „Jude angetreten" | |
| `--sleep-word` | `JUDE_SLEEP_PHRASE`, sonst „Jude Zapfenstreich" | |
| `--gui` | aus | Web-GUI über uvicorn (`main.py:146–147`) |
| `--desktop` | aus | Desktop-Fenster über `web.desktop.run_desktop` (`main.py:142–143`) |
| `--host` | `JUDE_HOST`, sonst `127.0.0.1` | |
| `--port` | `JUDE_PORT`, sonst **8765** | |
| `--verbose` | aus | Log-Stufe DEBUG statt INFO |

Der laufende Prozess auf diesem Rechner wurde als
`main.py --gui --host 127.0.0.1 --port 8765 --voice` vorgefunden — genau das,
was `start.sh:18` startet.

**Startwege:**

- `start.sh` (Projektwurzel): legt bei Bedarf `.venv` an, installiert
  `AGENT/requirements.txt`, prüft per `curl`, ob schon ein Server läuft, startet
  sonst im Hintergrund und öffnet das Cockpit als App-Fenster
  (`brave-browser` / `chromium` / `google-chrome` mit `--app=` und
  `--class=Jude`, sonst `xdg-open`).
- `autostart.sh`: dasselbe ohne Browserfenster, im Vordergrund
  (`exec … --gui … --voice`). Wird über `Jude-Autostart.desktop` mit 8 s
  Verzögerung beim Login gestartet.
- `Jude.desktop`: Startmenüeintrag, ruft `start.sh`.
- `start.bat` (Windows): eigene venv `.venv-win`, startet `--desktop --voice`.
- `AGENT/deploy/jarvis.service`: systemd-User-Unit — **nicht lauffähig**, siehe
  [Auffälligkeiten](#12-auffälligkeiten).

Im Textmodus (ohne `--gui`) versteht die Konsole vier Befehle
(`main.py:185–195`): `/quit`, `/status`, `/show-tool TOKEN`, `/approve-tool TOKEN`.

### 1.3 Was `build_application()` aufbaut

`AGENT/main.py:23–50`. Reihenfolge:

1. `.env` neben `main.py` laden (`main.py:24`).
2. `ToolRegistry()`, `ModelRouter()`, `ConfirmationQueue()` anlegen.
3. `load_all_tools(registry, router=…, confirmations=…)` — importiert jedes
   Modul unter `AGENT/tools/`, ruft dessen `register(registry)` und
   `register_context(registry, **context)` auf und lädt zusätzlich alle
   `*.py` aus `GENERATED_TOOLS_DIR` (`tools/__init__.py:15–44`).
   **Wichtig:** Erst `register_context` in `tools/skills.py:127` verbindet die
   Registry mit der Bestätigungswarteschlange (`registry.set_confirmations`).
   Ohne diesen Aufruf wäre das Sicherheits-Gate wirkungslos.
4. `ToolCreator` bauen und als Werkzeug `create_new_tool` registrieren.
5. `Agent(router, registry)` erzeugen, Profilfakten setzen (`_seed_profile`,
   `main.py:62–70`: `JUDE_USER_NAME` und die per Semikolon getrennten Einträge
   aus `JUDE_PROFILE` werden als bestätigte Erinnerungen abgelegt).
6. Nachträglich anhängen und deren Werkzeuge registrieren:
   `SubAgentService` (`agent.team`), `SchedulerService` (`agent.scheduler`),
   `BackupService` (`agent.backup`), `NotionDatabaseService`,
   `DocumentService` (`agent.documents`).
7. `_ensure_nightly_backup` (`main.py:53–59`) legt einmalig die tägliche
   Aufgabe „Nächtliche Sicherung" um 03:00 an, falls noch keine
   `run_backup`-Aufgabe existiert.

Rückgabe: `(agent, creator)`.

`web/app.py:56` ruft `build_application()` **auf Modulebene** — der Import des
Web-Moduls baut also die gesamte Anwendung auf.

### 1.4 Wo die Daten liegen

Zentrale Pfadauflösung: `AGENT/core/paths.py`. Die Wurzel wird in dieser
Reihenfolge bestimmt (`_detect_root`, `paths.py:21–33`):
`AI_DATA_ROOT` bzw. `JUDE_DATA_ROOT` → `/media/d4sd1ng/AI-Data` →
unter Windows Laufwerke D:–M: mit `Jude/`- und `Projects/`-Ordner →
vier Ebenen über `paths.py`.

| Konstante | Wert auf diesem Rechner | Inhalt |
|---|---|---|
| `AI_DATA_ROOT` | `/media/d4sd1ng/AI-Data` | Wurzel |
| `JUDE_DIR` | `…/AI-Data/Jude` | |
| `DATA_DIR` | `…/AI-Data/Jude/data` | DB, JSON-Zustände, Logs |
| `MODELS_DIR` | `…/AI-Data/Jude/models` | `whisper-base`, `whisper-small`, `piper`, `wakeword` |
| `IMAGES_DIR` | `…/AI-Data/Jude/images` | erzeugte Bilder + Metadaten-JSON |
| `MEALS_DIR` | `…/AI-Data/Essensplan` | Essensplan-PDFs |
| `CALENDAR_DIR` | `…/AI-Data/Kalender` | ICS-Dateien |
| `GENERATED_TOOLS_DIR` | `…/AI-Data/Jude/generated_tools` | selbst erzeugte Werkzeuge |
| `TEST_DATA_DIR`, `TEST_REPOS_DIR` | `…/Jude/test-data`, `…/Jude/test-repos` | nur für Tests |

Der Kommentar in `paths.py:11–12` fordert ausdrücklich: kein Modul außer
diesem darf absolute Datenpfade enthalten.

**Datenbank:** `DATA_DIR/jude.db` (`services/database.py:11`), SQLite im
WAL-Modus. Das Schema wird bei jedem `connection()`-Aufruf sichergestellt
(`database.py:123–133`), inklusive Spaltenmigrationen für `model_usage`
(`database.py:108–120`). Aktuell 18 Tabellen; Stichprobe vom 13.08.:

| Tabelle | Zeilen | Zweck |
|---|---|---|
| `scheduler_runs` | 8836 | Ausgeführte Kill-Zone-Läufe |
| `candles` | 2906 | OHLCV-Historie |
| `route_decisions` | 213 | Jede Routing-Entscheidung |
| `model_usage` | 269 | Jeder Modellaufruf mit Kosten |
| `conversation_turns` | 81 | Gesprächsverlauf (episodisches Gedächtnis) |
| `rag_chunks` | 41 | Dokumentabschnitte (3 Dokumente) |
| `notifications` | 26 | Benachrichtigungen |
| `agent_runs` | 16 | Sub-Agenten-Läufe |
| `audit_log` | 14 | Sicherheitsrelevante Ereignisse |
| `confirmations` | 11 | Bestätigungs-Warteschlange |
| `fact_checks` | 8 | Fakten-Prüfungen |
| `memory_items` | 3 | Langzeitgedächtnis |
| `meal_plans` | 1 | Essenspläne |
| `reviews` | **0** | Abnahmen — noch nie befüllt |
| `calendar_events`, `trading_cards`, `memory_blocks` | 0 | |

**Logdatei:** `DATA_DIR/logs/jude.log`, rotierend, 5 MB je Datei, 5 Sicherungen
(`main.py:104–127`). Auf dem Bildschirm erscheinen nur Warnungen und Fehler
(`main.py:116`), die Datei bekommt INFO bzw. mit `--verbose` DEBUG.

**JSON-Zustände unter `DATA_DIR`:**
`sub_agents.json` (Team), `scheduled_tasks.json` (Dienstplan),
`voice_briefing.json` (letztes volles Sprachbriefing),
`groq_kontingent.json` (Groq-Restkontingent, wird erst beim ersten
Groq-Aufruf angelegt), `sub_agent_memory/*.json` (Notizen je Mitarbeiter),
`sub_agent_lessons/*.json` (Beanstandungen je Mitarbeiter).

**Sicherungen:** `JUDE_DIR/backups/jude-backup-*.zip`, 14 Stück werden behalten.

---

## 2. Modell-Routing

Dateien: `AGENT/core/model_router.py` (729 Z.), `AGENT/config/models.yaml` (190 Z.),
`AGENT/services/kontingent.py` (145 Z.).

### 2.1 Konfigurierte Modelle

`config/models.yaml:1–137`. Elf Modelle:

| Name | Anbieter | Modell | Kontext | Tags | Prio |
|---|---|---|---|---|---|
| `local_qwen_coder` | ollama | `qwen3:8b` | 16 384 | lokal, privat, schnell, **tools**, code, test, routing_judge, medien | 10 |
| `local_dolphin` | ollama | `dolphin3:8b` | 8 192 | lokal, privat, **unzensiert**, allgemein, kreativ | 9 |
| `cloud_openai_luna` | openai | `gpt-5.6-luna` | 1 050 000 | cloud, schnell, kostenguenstig, extraktion, routing, tools | 7 |
| `cloud_openai_terra` | openai | `gpt-5.6-terra` | 1 050 000 | cloud, analyse, code, tools, grosserkontext | 8 |
| `cloud_openai_sol` | openai | `gpt-5.6-sol` | 1 050 000 | cloud, **top_tier**, analyse, code, agentisch | 10 |
| `cloud_claude_sonnet` | anthropic | `claude-sonnet-5` | 1 000 000 | cloud, analyse, code, agentisch, tools | 8 |
| `cloud_claude_fable` | anthropic | `claude-fable-5` | 1 000 000 | cloud, **top_tier**, langlaufend, agentisch, tools | 10 |
| `cloud_deepseek_chat` | deepseek | `deepseek-chat` | 128 000 | cloud, kostenguenstig, analyse, code, tools | 8 |
| `cloud_groq_llama` | groq | `llama-3.3-70b-versatile` | 128 000 | cloud, schnell, allgemein, analyse — **kein `tools`** | 8 |
| `cloud_openrouter_dolphin` | openrouter | `dolphin3.0-mistral-24b` | 32 768 | cloud, unzensiert, allgemein, kreativ | 7 |
| `cloud_gemini_flash` | google | `gemini-3.6-flash` | 1 000 000 | cloud, schnell, multimodal, tools | 8 |

Das fehlende `tools`-Tag bei Groq ist gemessen, nicht vermutet: der Kommentar
in `models.yaml:107–112` hält fest, dass llama-3.3 den Werkzeugaufruf als
Fließtext ausgibt statt ihn abzusetzen — zwei von zwei Versuchen. Für alles
ohne Werkzeuge bleibt es die erste Wahl.

Adapter je Anbieterformat (`model_router.py:67–359`): `OllamaAdapter`,
`OpenAIAdapter` (Responses-API), `OpenAICompatAdapter` (Chat-Completions —
OpenRouter, Groq, DeepSeek), `AnthropicAdapter`, `GoogleAdapter`. Jeder
normalisiert Antwort und Verbrauchszahlen auf ein gemeinsames Format.

### 2.2 Auswahl (`select_model`, `model_router.py:456–489`)

1. **Kandidaten filtern:** nur Modelle, deren Anbieter aktiv ist
   (`_provider_enabled`, Z. 449–454). Ollama ist immer aktiv; alle anderen
   brauchen `JUDE_PAID_MODELS_ENABLED` **und** den jeweiligen Schlüssel.
2. **`local_first`:** In `models.yaml:142` steht `local_first: true`. Damit
   wird die Kandidatenliste in Z. 460–461 **immer** auf lokale Modelle
   reduziert. Ein Cloud-Modell wird folglich nie *ausgewählt* — es kann nur
   über die Fallback-Kette erreicht werden.
3. **Unzensiert:** enthält die Nutzereingabe das Wort „unzensiert"
   (`agent.py:80`), bleiben nur Modelle mit dem Tag `unzensiert` übrig.
4. **Werkzeugbedarf:** Bei `needs_tools` und einer erkennbaren Handlungsabsicht
   (Aufgabentyp ≠ „allgemein" oder Treffer im `_ACTIONABLE`-Regex,
   Z. 408–418) wird auf Modelle mit `tools`-Tag verengt.
5. **Bewerten** (`score`, Z. 479–487): lokale Vorliebe (+2), Passung des
   Aufgabentyps zu den Tags (+7 bzw. +4), unzensiert-Bonus außerhalb von
   Code/Medien (+2), Standardmodell-Bonus (+6), Priorität, minus Latenz/300,
   minus Kosten × 100, plus gelernte Anpassung — alles mal `weight`.
6. **Gelernte Anpassung** (`_learned_adjustment`, Z. 440–447): Erst ab drei
   bewerteten Läufen derselben Kombination Aufgabentyp/Modell fließt der
   Mittelwert des Nutzerfeedbacks mit Faktor 4 ein.

Aufgabentypen (`task_type`, Z. 424–438) werden über Stichwortlisten bestimmt:
`code`, `trading`, `research`, `document`, `home`, `medien`, sonst `allgemein`.

Standardmodell ist `local_dolphin` (`models.yaml:143`). Nur echte Werkzeug-,
Code- oder Medienanfragen dürfen `local_qwen_coder` zur Basis machen
(`code_or_tools`, Z. 476–477); Plaudern, Analyse und Kreatives bleiben bei
dolphin.

### 2.3 Fallback-Kette

`models.yaml:156–164`, aufgelöst in `_resolve_fallbacks`
(`model_router.py:491–508`):

```
tags:lokal            dolphin3 + qwen3        frei, lokal
cloud_groq_llama      Groq 70B                frei, erste echte Verstärkung
cloud_deepseek_chat   DeepSeek                günstig    ← Schlüssel fehlt
cloud_openai_terra    gpt-5.6-terra           erste bezahlte Stufe
cloud_claude_sonnet   claude-sonnet-5         stark
cloud_openai_sol      gpt-5.6-sol             Top-Tier
cloud_claude_fable    claude-fable-5          Top-Tier, teuerste Stufe
cloud_openrouter_dolphin                      unzensierte Reserve ← Schlüssel fehlt
```

Zwei Filter in der Kette:

- Sobald Werkzeuge im Spiel sind, werden Stufen ohne `tools`-Tag
  übersprungen (Z. 504–505). Begründung im Code: ein Lauf fiel auf dolphin3
  zurück und brauchte 679 Sekunden für nichts.
- Im unzensierten Pfad bleiben nur Stufen mit dem Tag `unzensiert` (Z. 506).

Der Ablauf je Stufe (`call_with_fallback`, Z. 592–663):

1. `_cloud_affordable` (Z. 526–532): geschätzte Kosten gegen
   `request_cost_limit` und Monatsbudget. Verstoß → Status `skipped_cost_limit`.
2. `_groq_verfuegbar` (Z. 510–524): nur für Groq, siehe unten. Verstoß →
   `skipped_kontingent`.
3. Aufruf. Danach eine Qualitätsprüfung, wenn das Modell **nicht** `top_tier`
   ist **und** eine stärkere Stufe existiert (Z. 642–644).
   `_local_quality_check` (Z. 542–561) lehnt offensichtlich unbrauchbare
   Antworten sofort ab (leer, „ich kann dabei nicht", „Fehler:" …) und lässt
   ab Komplexität 6 ein Modell mit dem Tag `routing_judge` — das ist qwen3 —
   per JSON urteilen. Fällt die Prüfung durch, geht es eine Stufe höher.
4. Jede Entscheidung wird in `route_decisions` protokolliert, jeder Aufruf in
   `model_usage`.

### 2.4 `force_model`

`call_with_fallback(…, force_model=…)`, Z. 601–610. Sub-Agenten geben ihr
Modell fest vor, weil die Heuristik für Werkzeugketten das Basismodell wählte.
Schutz: Ist ein fest vorgegebenes Modell nicht `tools`-fähig, obwohl Werkzeuge
übergeben wurden, wird die Vorgabe verworfen und die Heuristik entscheidet
wieder (Z. 607–610).

Nicht geprüft wird dagegen, ob der Anbieter des vorgegebenen Modells überhaupt
aktiv ist — fehlt der Schlüssel, scheitert erst der Aufruf und die Kette
rutscht zur nächsten Stufe. Genau das ist am 13.08. um 21:10 passiert: Heinz
(vorgegeben `cloud_groq_llama`) lief auf `local_qwen_coder`, weil das
Groq-Kontingent erschöpft war.

### 2.5 Kostengrenzen

| Größe | Quelle | Vorgabe |
|---|---|---|
| Monatsbudget | `JUDE_CLOUD_BUDGET_USD`, sonst `models.yaml:140` | 5,00 USD |
| Grenze je Anfrage | `JUDE_CLOUD_REQUEST_LIMIT_USD`, sonst `models.yaml:146` | 1,00 USD |
| Cloud überhaupt erlaubt | `JUDE_PAID_MODELS_ENABLED` | `true` |

Das Budget wird beim Start aus `model_usage` des laufenden Monats gelesen
(Z. 391–394) und bei jedem Aufruf fortgeschrieben (Z. 577). Kosten werden
getrennt nach ungecachtem Input, gecachtem Input, Cache-Schreibvorgängen
(×1,25) und Output berechnet (Z. 575–576).

Tatsächlicher Verbrauch bis 13.08.: **0,1976 USD**, entstanden durch einen
einzigen Aufruf an `claude-sonnet-5`. 16 Groq-Aufrufe und 252 lokale Aufrufe
waren kostenfrei.

### 2.6 Groq-Kontingentüberwachung

`services/kontingent.py`. Groqs freie Stufe gibt 100 000 Token pro Tag; ein
einzelner langer Agentenlauf kann den Rest eines Tages verbrauchen. Der Modul-
kopf hält als Messung fest: am 13.08. um 20:55 standen 94 069 von 100 000 auf
der Uhr, ohne dass es irgendwo sichtbar war.

**Messen.** Der `OpenAICompatAdapter` gibt bei Anbieter `groq` jede Antwort an
das Modul weiter (`model_router.py:213–219`):
- Statuscode 429 → `grenze_merken(response.text)` liest per Regex `Used N`,
  `Limit N` und `try again in …` aus dem Fehlerrumpf und speichert daraus Rest,
  Limit und eine Sperrzeit (`kontingent.py:83–107`).
- Sonst → `kopfzeilen_merken(response.headers)` übernimmt
  `x-ratelimit-remaining-tokens` und `x-ratelimit-limit-tokens`
  (`kontingent.py:59–80`). Das ist die belastbarere Zahl, weil sie auch
  Anfragen kennt, die nicht über Jude liefen.

Der Stand liegt in `DATA_DIR/groq_kontingent.json`.

**Bremsen.** `verfuegbar(geschaetzte_token)` (`kontingent.py:135–145`) meldet
`False`, wenn eine gemeldete Sperre läuft oder der Rest unter die Reserve
fiele. Die Reserve sind 15 % des Tageslimits (`RESERVE_ANTEIL`, Z. 32) — sie
bleibt für Judes Chefprüfung und den Redakteur stehen. Der Router schätzt den
Bedarf als „Zeichen aller Nachrichten ÷ 4 plus min(max_tokens, 2048)"
(`model_router.py:517–518`) und überspringt die Stufe bei Unterdeckung.

Jede Ausnahme in dieser Buchhaltung wird verschluckt (`kontingent.py:56`,
`model_router.py:523–524`): sie darf einen Lauf nie scheitern lassen.

`stand()` (Z. 110–132) liefert Limit, Rest, Verbrauch, Prozentsatz, Reserve,
Sperrstatus und Messzeitpunkt — die GUI zeigt das über `/api/status`.

---

## 3. Werkzeuge

### 3.1 Wie ein Werkzeug definiert wird

`AGENT/core/tool_registry.py`. Ein Werkzeug ist ein `Tool`-Dataclass
(Z. 9–25) mit fünf Feldern:

| Feld | Bedeutung |
|---|---|
| `name` | muss ein gültiger Python-Bezeichner sein (`register`, Z. 43) |
| `description` | geht als Beschreibung an das Modell |
| `func` | die aufgerufene Funktion; Argumente kommen als Schlüsselwörter |
| `param_schema` | vollständiges JSON-Schema |
| `confirm_action` | optional: Aktionstyp für das Bestätigungs-Gate |
| `untrusted` | optional: Rückgabe stammt aus externer Quelle |

`to_openai_format()` (Z. 22–25) erzeugt daraus das Function-Calling-Schema.

Registriert wird entweder direkt (`registry.register(Tool(...))`) oder über
den Dekorator `register_function` (Z. 47–51).

**Zwei Sicherheitsmechanismen in `execute()` (Z. 61–83):**

- *Bestätigungs-Gate*: Hat das Werkzeug ein `confirm_action` und ist eine
  Warteschlange gesetzt, wird der Aufruf **nicht ausgeführt**, sondern als
  Bestätigung vorgemerkt. Zurück geht ein Hinweistext mit der ID
  (Z. 70–76). Das schützt gegen Prompt-Injection über Web-, Datei- und
  Bildinhalte.
- *Untrusted-Markierung*: Ist `untrusted` gesetzt, wird die Rückgabe in einen
  Rahmen gelegt, der sie ausdrücklich als Daten und nicht als Anweisungen
  kennzeichnet (`_UNTRUSTED_WRAPPER`, Z. 28–31).

Ein fehlgeschlagenes Werkzeug wirft **keine Ausnahme**, sondern gibt den Fehler
als Text zurück (Z. 79–80). Diese Entscheidung ist der Grund, warum die
Laufbewertung des Teams die Rückgabewerte einzeln prüfen muss (siehe
[Abschnitt 4.5](#45-laufbewertung)).

### 3.2 Das Bestätigungs-Gate im Ganzen

```
Modell ruft Werkzeug              tool_registry.execute()
   │                                   │
   │ confirm_action gesetzt?  ──ja──►  ConfirmationQueue.request()
   │                                   │  Zeile in confirmations, Status pending
   │                                   ▼
   │                              GUI-Tab „Bestätigungen"
   │                                   │
   │                              Tino: approve / reject
   │                                   ▼
   │                              ConfirmationQueue.decide()
   │                                   │  Status executing (atomar, Z. 61–63)
   │                                   ▼
   └── nein ──► direkt ausführen   ActionExecutor.__call__()
```

Erlaubte Aktionstypen: `ConfirmationQueue.ALLOWED_ACTIONS`
(`services/confirmations.py:14–17`) — 20 Typen von `mail_send` über
`code_push` und `ssh_command` bis `shell_command`. Alles andere wird
abgewiesen (Z. 20–21).

Ausgeführt wird über `ActionExecutor` (`services/actions.py:19–88`), eine
reine Zuordnung von Aktionstyp zu Dienstmethode. Sonderfall `shell_command`
(Z. 76–88): läuft mit `shell=True`, aber nur wenn `JUDE_PRIVILEGED` nicht auf
`false` steht, und wird im Audit-Log vermerkt. Die Begründung im Code: der
genaue Befehl wurde vom Nutzer im Bestätigungsdialog freigegeben.

### 3.3 Liste aller Werkzeuge

74 Werkzeuge in der globalen Registry. Spalte **B** = Bestätigung nötig,
**U** = Rückgabe als nicht vertrauenswürdig markiert.

#### Markt und Trading (`tools/skills.py`)

| Werkzeug | | Zweck |
|---|---|---|
| `market_fetch` | | OHLCV für BTC/EUR, BTC/USD, XAU/EUR, XAU/USD abrufen und speichern |
| `market_history` | | Gespeicherte OHLCV-Historie lesen |
| `ict_status` | | Zustand des lokalen MT5-Demo-Stacks und des Kill-Zone-Schedulers |
| `ict_analyse_live` | | H4/H1/M1 gemeinsam analysieren und eine Trading Card speichern |
| `ict_training_status` | | Walk-forward-Trainingsstand für XAUUSD und BTCUSD lesen |

#### Nachrichten, Web, Fakten

| Werkzeug | | Zweck |
|---|---|---|
| `web_search` | U | Sofortantwort von DuckDuckGo, sonst Treffer aus Google News |
| `news_search` | U | Nachrichten zu einem Stichwort: Schlagzeile, Quelle, Datum |
| `crypto_news` | U | Crypto-News über den konfigurierten Nachrichtendienst |
| `crypto_news_brief` | | Crypto-News abrufen und journalistisch mit Quellen einordnen |
| `scrape_public_url` | U | Öffentliche URL sicher extrahieren; keine Logins, keine Paywalls |
| `fact_check_url` | | Bericht, Post oder Video per URL gegen unabhängige Quellen prüfen |

#### E-Mail

| Werkzeug | | Zweck |
|---|---|---|
| `mail_search` | | Konfiguriertes Postfach durchsuchen |
| `mail_read` | U | Eine E-Mail lesen |
| `mail_draft` | | Entwurf anlegen, aber nicht senden |
| `mail_archive` | B | E-Mail archivieren |
| `request_mail_send` | | Versand zur Bestätigung vormerken |
| `request_mail_delete` | | Löschung zur Bestätigung vormerken |

#### Code und Dateien

| Werkzeug | | Zweck |
|---|---|---|
| `coding_repositories` | | Git-Repositories unter AI-Data inventarisieren |
| `coding_status` | | Git-Status eines Repositories lesen |
| `coding_diff` | | Git-Diff lesen |
| `coding_test` | | Vorhandene Tests eines Repositories ausführen |
| `coding_read` | U | Textdatei read-only lesen (auch außerhalb AI-Data) |
| `coding_write` | B | Textdatei ausschließlich unter AI-Data atomar schreiben |
| `coding_branch` | B | `codex/`-Branch erstellen |
| `coding_commit` | B | Explizit genannte Pfade committen |
| `coding_push` | B | Branch zu origin pushen |
| `coding_clone` | B | Repository nach AI-Data/Projects klonen |
| `coding_pull` | B | Fast-forward-Pull |
| `coding_create_pr` | B | GitHub-Pull-Request erstellen |
| `list_directory` | | Verzeichnis auflisten; System- und Papierkorbpfade gesperrt |
| `read_project_file` | | UTF-8-Datei innerhalb des Jude-Projekts lesen |
| `request_external_write` | | Schreiben außerhalb AI-Data zur Bestätigung vormerken |
| `create_new_tool` | | Geprüften Entwurf für ein neues Werkzeug erzeugen |

#### Fernzugriff und System

| Werkzeug | | Zweck |
|---|---|---|
| `list_ssh_hosts` | | Freigegebene SSH-Hosts auflisten |
| `ssh_run` | B | Befehl auf einem freigegebenen Host ausführen (schlüsselbasiert) |
| `scp_transfer` | B | Datei per SCP übertragen (`upload`/`download`) |
| `system_health` | | Ollama, Modelle, Speicher, DB, Mikrofon, Budget, letzte Fehler |
| `request_command` | | Systembefehl zur ausdrücklichen Bestätigung vormerken |
| `request_confirmation` | | Beliebige risikoreiche Aktion vormerken |

#### Haus und Alltag

| Werkzeug | | Zweck |
|---|---|---|
| `light_switch` | B | Licht in Wohnzimmer, Schlafzimmer oder Flur schalten |
| `home_action_status` | | Konfigurierte Alexa- und Growcontroller-Aktionen auflisten |
| `home_action_run` | B | Ausschließlich eine Aktion aus der Allowlist ausführen |
| `rain_radar` | | Regenradar-Frames für den konfigurierten Standort |
| `get_current_weather` | | Aktuelles Wetter über Open-Meteo |
| `calendar_list` | | Lokal gespeicherte Termine auflisten |
| `request_calendar_event` | | Termin als ICS zur Bestätigung vormerken |
| `meal_plan` | | Günstigen Low-Carb-Plan mit PDF und Einkaufsliste erstellen |
| `shopping_compare` | | G-Star-/Nike-Herrenbekleidung vergleichen; keine Bestellung |

#### Bilder und 3D

| Werkzeug | | Zweck |
|---|---|---|
| `generate_image` | | Bild aus Text über OpenAI erzeugen, lokal unter `Jude/images` ablegen |
| `analyze_image` | | Ein gegebenes Bild beschreiben oder eine Frage dazu beantworten |
| `render_3d_objects` | | 3D-Szene lokal aus einer Objektliste rendern (bevorzugt) |
| `render_3d_scene` | | 3D-Szene aus einem vollständigen bpy-Skript rendern |
| `ocr_file` | U | Deutschen/englischen Text aus Bild oder PDF lesen |

#### Wissen und Gedächtnis

| Werkzeug | | Zweck |
|---|---|---|
| `ingest_document` | | Text oder PDF unter AI-Data ins Wissen einlesen |
| `search_documents` | U | Eingelesene Dokumente semantisch durchsuchen |
| `list_documents` | | Eingelesene Dokumente auflisten |
| `forget_document` | | Dokument aus dem Wissen entfernen |
| `memory_list` | | Bestätigte Erinnerungen und Kandidaten auflisten |
| `memory_stats` | | Statistik von Gedächtnis und freigegebenen Trainingsgesprächen |
| `recall_conversations` | | Frühere Gespräche zu einem Thema wiederfinden |

#### Notion

| Werkzeug | | Zweck |
|---|---|---|
| `notion_databases` | | Zeigt, welche Datenbanken eingerichtet sind (ohne Netzzugriff) |
| `notion_schema` | | Feldnamen, Feldtypen und erlaubte Auswahlwerte — vor jedem Schreiben |
| `notion_query` | U | Einträge lesen, optional nach Text gefiltert |
| `notion_create` | | Neuen Eintrag anlegen |
| `notion_update` | | Felder eines vorhandenen Eintrags ändern |

#### Team, Zeitsteuerung, Sicherung

| Werkzeug | | Zweck |
|---|---|---|
| `list_sub_agents` | | Mitarbeiter mit Rolle und Skills auflisten |
| `list_available_skills` | | Werkzeuge auflisten, die ein Mitarbeiter bekommen kann |
| `create_sub_agent` | B | Neuen Mitarbeiter anlegen |
| `delegate_to_agent` | | Einem Mitarbeiter eine Aufgabe übergeben und das Ergebnis erhalten |
| `list_scheduled_tasks` | | Zeitgesteuerte Aufgaben auflisten |
| `create_scheduled_task` | | Aufgabe anlegen (`prompt`, `briefing` oder `tool`) |
| `delete_scheduled_task` | | Aufgabe entfernen |
| `run_backup` | | Sofort Datenbank und Konfiguration sichern |
| `list_backups` | | Vorhandene Sicherungen auflisten |

#### Nur innerhalb eines Sub-Agenten (nicht global)

| Werkzeug | Quelle | Zweck |
|---|---|---|
| `remember_finding` | `services/team.py:326–336` | Dauerhafte Notiz festhalten |
| `submit_for_review` | `services/team.py:302–324` | Fertiges Erzeugnis zur Abnahme vorlegen |
| `write_copy` | `services/team.py:217–300` | Heinz, den Redakteur, direkt mit einem Text beauftragen |

### 3.4 Selbst erzeugte Werkzeuge

`AGENT/core/tool_creator.py`. Zweistufig: `generate_draft` lässt ein Modell ein
Plugin schreiben und prüft es statisch (`_validate`, Z. 85–147) gegen eine
enge Whitelist — erlaubte Importe (Z. 20), verbotene Aufrufe
(`eval`, `exec`, `open`, `getattr` …, Z. 21), verbotene Attribute
(`system`, `popen`, `unlink`, `write_text`, `connect`, `request`, Z. 23), keine
Dekoratoren, keine Dunder-Namen, auf Modulebene nur Literale. Erst `approve`
(Z. 65–78) führt zusätzlich einen Sandbox-Test in Docker aus
(`--network none --read-only --cap-drop ALL`, Z. 156–161) und schreibt die
Datei nach `GENERATED_TOOLS_DIR`.

Bedienung ausschließlich über die Konsole: `/show-tool TOKEN`,
`/approve-tool TOKEN`. In der GUI gibt es dafür keinen Weg.

---

## 4. Das Team

Dateien: `AGENT/services/team.py` (598 Z.),
`/media/d4sd1ng/AI-Data/Jude/data/sub_agents.json`,
Anlage über `AGENT/scripts/team_setup.py`.

### 4.1 Die neun Mitarbeiter

| Kürzel | Person | Alter | Modell | Werkzeuge | Rolle in einem Satz |
|---|---|---|---|---|---|
| `redakteur` | **Heinz** | 49 | `cloud_groq_llama` | *keine* | Schreibt alle Texte des Hauses und gibt ausschließlich den fertigen Text zurück. |
| `scraper` | **Rasmus** | 29 | `local_qwen_coder` | `news_search`, `scrape_public_url`, `ingest_document`, `search_documents`, `notion_schema`, `notion_create` | Sammelt dublettenfrei Tech-Meldungen der letzten 24 h und legt sie als Content-Ideen ab. |
| `social` | **Mike** | 26 | `local_qwen_coder` | `search_documents`, `notion_schema`, `notion_query`, `notion_create`, `notion_update` | Schreibt kurze Plattformbeiträge für Nurovelle und Politara und legt sie zur Abnahme vor. |
| `content` | **Stefan** | 41 | `local_qwen_coder` | `news_search`, `search_documents`, `notion_schema`, `notion_query`, `notion_create`, `notion_update` | Macht aus vorhandenen Ideen tragfähige Langformate mit Gliederung. |
| `sequencer` | **Tom** | 47 | `local_qwen_coder` | `notion_schema`, `notion_query`, `notion_create`, `notion_update`, `mail_draft` | Baut und terminiert die E-Mail-Sequenzen in Notion — versendet nie. |
| `outreach` | **Bernd** | 31 | `local_qwen_coder` | `notion_schema`, `notion_query`, `notion_update`, `scrape_public_url` | Beschafft Kontaktadressen aus Impressum und Kontaktseite, fünf Firmen je Lauf. |
| `leadmanager` | **Frieda** | 38 | `local_qwen_coder` | `notion_schema`, `notion_query`, `notion_update`, `mail_read`, `mail_search` | Führt den Lead-Bestand und schreibt Status fort, ausschließlich bei Einträgen mit Adresse. |
| `designer` | **Heike** | 33 | `local_qwen_coder` | `generate_image`, `notion_schema`, `notion_query`, `notion_update` | Erzeugt Bildmotive — aber erst für Beiträge, die abgenommen sind. |
| `engineer` | **Joana** | 35 | `local_qwen_coder` | `coding_read`, `coding_write`, `coding_diff`, `coding_status`, `coding_test`, `read_project_file`, `list_directory` | Ändert Code auf Zuruf, klein und getestet; committet und pusht nie. |

Jede Rollenbeschreibung folgt demselben Schema (`scripts/team_setup.py:11–17`):
gemeinsamer Rahmen (Nurovelle-Geschäftsmodell) plus
**QUELLE / ARBEIT / ZIEL / FERTIG / NICHT**.

Fünf Mitarbeiter dürfen Heinz direkt beauftragen (`TEXTER`, `team.py:46`):
`social`, `content`, `sequencer`, `scraper`, `leadmanager`. Bernd trägt nur
Adressen ein, Heike macht Bilder, Joana schreibt Code — die brauchen keinen
Redakteur.

### 4.2 Warum zwei Modelle

Der Kommentar in `team.py:29–40` hält die Messung fest: qwen3:8b setzte in
10 von 10 Läufen einen echten Werkzeugaufruf ab (Median 115 s), Groqs
llama-3.3 in 0 von 2 — es schreibt den Aufruf als Fließtext hin. Umgekehrt
schreibt das 70B-Modell sprachlich deutlich besser als das 8B.

Konsequenz: Werkzeugarbeit läuft lokal auf qwen3 (`STANDARD_MODELL`),
Textarbeit und Judes Chefprüfung laufen auf Groq (`TEXT_MODELL`) — und zwar
konsequent **ohne** Werkzeuge, also auf dem Pfad, auf dem Groq nie gescheitert
ist.

### 4.3 Wie ein Sub-Agent gebaut wird

`_build_agent` (`team.py:338–393`):

1. Neue, leere `ToolRegistry`; die Bestätigungswarteschlange des Hauptagenten
   wird übernommen (Z. 342) — sicherheitsrelevante Aktionen laufen also über
   dasselbe Gate.
2. Modell bestimmen; prüfen, ob es `tools` im Tag hat (Z. 348–349).
   **Ist es nicht werkzeugfähig, bekommt der Mitarbeiter gar keine Werkzeuge**
   und `max_tool_steps = 0`. Sonst entstünde genau der Schaden, der Mike
   lahmgelegt hat: das Modell schreibt den Aufruf hin, nichts wird abgelegt,
   und die Antwort sieht trotzdem aus wie Arbeit.
3. Nur die in `skills` genannten Werkzeuge werden übernommen, dazu
   automatisch `remember_finding` und `submit_for_review`, für die fünf
   Texter zusätzlich `write_copy`.
4. Systemprompt zusammensetzen: Identität („Du heißt Heike und bist 33 Jahre
   alt"), Rolle, dann in dieser Reihenfolge angehängt:
   - **offene Revisionen** — „ZUERST ERLEDIGEN" (Z. 370–378),
   - **Lehren** — „DAS WURDE DIR SCHON BEANSTANDET" (Z. 379–386),
   - **Notizen** — „Was du bisher festgehalten hast" (Z. 387–390).
5. `Agent(router, sub, system_prompt=…, max_tool_steps=16, force_model=…)`.

Die Sicherheitsregeln des Hauptagenten bleiben verbindlich: `Agent.__init__`
hängt `_SECURITY` (`core/agent.py:17–22`) auch an jeden Sub-Agenten-Prompt an.

### 4.4 Gedächtnis und Lehren

**Notizen** (`remember`, `team.py:132–157`) liegen in
`DATA_DIR/sub_agent_memory/<name>.json`, höchstens 500 je Mitarbeiter, davon
die letzten 40 im Prompt. Störungsmeldungen werden aktiv abgewiesen
(`KEINE_NOTIZ`, Z. 53–55): Bernds Gedächtnis bestand einmal aus drei
Fehlermeldungen und einem Platzhalter, und beim nächsten Lauf las er als „was
ich bisher weiß", dass nichts geht. Störungen gehören in die Blocker des Laufs,
nicht ins Gedächtnis.

**Lehren** (`lehre_merken`, Z. 182–209) liegen in
`DATA_DIR/sub_agent_lessons/<name>.json`, höchstens 40, davon die 12
häufigsten im Prompt. Gleichlautende Beanstandungen werden über eine
normalisierte Vergleichsform (`_kern`, Z. 177–180) zusammengefasst und
gezählt; was oft kam, steht oben und wird im Prompt mit „(schon 3x
beanstandet)" ausgewiesen.

Beanstandungen entstehen an drei Stellen:
- Judes Chefprüfung bei einer Revision (Z. 556),
- Tinos Anmerkung bei einer Revision in der GUI (`web/app.py:692–693`) —
  „Tinos Anmerkung wiegt schwerer als jede andere",
- die Auftragsprüfung, wenn ein Texter Heinz ohne jede Empfängerangabe
  beauftragt (Z. 250–259).

### 4.5 Laufbewertung

`run` (`team.py:395–465`) legt eine Mitschrift um `agent.tools.execute`
(Z. 413–420): jeder Werkzeugname und jede Fehlermeldung wird protokolliert.

Der Grund steht im Code (Z. 406–410): Die Werkzeugschicht gibt Fehler als Text
zurück statt zu werfen, der Agent formuliert daraus eine höfliche Antwort — und
**11 von 11 Läufen standen auf „abgeschlossen", obwohl 6 nichts zustande
gebracht hatten**.

`_bewerten` (Z. 477–502) wertet deshalb ausschließlich Gemessenes aus. Der
Antworttext wird bewusst ignoriert, damit ein Modell seinen Misserfolg nicht
schönschreiben kann:

| Status | Bedingung |
|---|---|
| `abgeschlossen` | Alle Werkzeugaufrufe gingen durch — oder der Agent hat gar keine Werkzeuge (Heinz; sein Ergebnis *ist* der Text) |
| `teilweise` | Einzelne Werkzeuge scheiterten, **oder es wurde gar keines benutzt** |
| `fehlgeschlagen` | Kein Werkzeug hat funktioniert, oder der Lauf warf eine Ausnahme |

Erkannt wird ein Fehlschlag über `FEHLERMUSTER` (Z. 468–470) — genau die
Texte, mit denen `ToolRegistry.execute` einen Fehlschlag meldet.

### 4.6 Rückgabe und Protokollierung

Die Rückgabe folgt einem festen Vertrag (Z. 442–465): `agent_id`, `task_id`,
`status`, `output`, `blockers`, `token_usage` sind Pflicht; dazu `agent`,
`person`, `alter`, `role`, `skills`, `model`, `duration_ms`, `tool_calls` und
`chefpruefung`. Das Feld `answer` bleibt zusätzlich erhalten, damit GUI und
Werkzeuge nicht umgestellt werden mussten.

`_protokollieren` (Z. 570–588) schreibt jeden Lauf in die Tabelle `agent_runs`:
Zeitpunkt, Mitarbeiter, Person, Aufgabe, Status, Antwort, Blocker, Modell,
Tokenverbrauch, Kosten, Dauer und die Liste der benutzten Werkzeuge. Ein
Protokollfehler darf den Lauf nie scheitern lassen (Z. 587–588).

Der Verbrauch je Lauf wird als Differenz des Monatszählers vor und nach dem
Lauf ermittelt (`router_verbrauch`, Z. 590–598).

---

## 5. Abnahme und Freigabe

Datei: `AGENT/services/review.py` (161 Z.). Der Modulkopf grenzt selbst ab:

> Abzugrenzen von `services.confirmations`: jene Warteschlange **blockiert**
> eine Aktion, bis Tino zustimmt. Hier meldet ein Mitarbeiter ein fertiges
> Erzeugnis zur Abnahme und **arbeitet sofort weiter**.

### 5.1 Der Weg

```
Mitarbeiter: submit_for_review   →  Status pruefung
                                      │
Jude (Chefprüfung, nach dem Lauf)  ───┤
                                      ├─ freigeben()  →  offen      (jetzt bei Tino)
                                      └─ revision()   →  revision   (zurück zum Verfasser)
                                                             │
Tino in der GUI                                              │
   ├─ abnehmen()   →  abgenommen                             │
   └─ revision()   →  revision  ───────────────────────────► beim nächsten Lauf
                                                              im Systemprompt
```

Tinos Liste zeigt ausschließlich `offen`. Was Jude zurückgewiesen hat, erreicht
ihn gar nicht erst.

Erlaubte Arten (`ARTEN`, Z. 35–36): `post`, `email`, `newsletter`, `sequenz`,
`dokument`, `recherche`, `grafik`, `sonstiges`. Vier davon bekommen im Cockpit
eine eigene Ampel (`COCKPIT_ARTEN`, Z. 40): `grafik`, `post`, `email`,
`newsletter`.

Die Tabelle `reviews` wird beim ersten Zugriff selbst angelegt (`_ensure`,
Z. 45–56) — sie steht nicht im zentralen Schema in `database.py`.

### 5.2 Methoden

| Methode | Zeile | Wirkung |
|---|---|---|
| `vorlegen(agent, art, titel, inhalt, quelle, person)` | 63 | Legt mit Status `pruefung` an. Titel ist Pflicht, Inhalt wird auf 12 000 Zeichen gekappt. Blockiert nicht. |
| `zur_pruefung(limit)` | 82 | Was beim Chef liegt |
| `freigeben(id, anmerkung)` | 86 | → `offen` |
| `offene_revisionen(agent)` | 90 | Was dieser Mitarbeiter überarbeiten soll — geht in seinen Prompt |
| `liste(status, limit, art)` | 99 | Für die GUI; liefert nur einen 700-Zeichen-Auszug plus Gesamtlänge |
| `zeigen(id)` | 112 | Volltext |
| `abnehmen(id, anmerkung)` | 119 | → `abgenommen` |
| `revision(id, anmerkung)` | 122 | → `revision`; **Anmerkung ist Pflicht** |
| `erledigt(id)` | 129 | → `offen`, Runde +1 — **wird nirgends aufgerufen**, siehe Auffälligkeiten |
| `zusammenfassung()` | 146 | Zählung je Status |
| `offen_nach_art()` | 151 | Speist die vier Cockpit-Ampeln; jede Art ist immer enthalten, auch mit 0 |

### 5.3 Judes Chefprüfung

`_chefpruefung` (`services/team.py:518–568`). Läuft **nach** dem Lauf des
Mitarbeiters, nicht währenddessen — er ist dann längst fertig und wartet auf
nichts.

Ablauf je Vorlage:
1. Volltext holen, Prüffrage bauen mit dem Maßstab `CHEF_MASSSTAB`
   (Z. 505–516): Marke Nurovelle, „Autonova" nirgends; durchgehend Deutsch,
   keine Platzhalter, kein abgeschnittener Satz; kein Werbedeutsch
   („Lösung", „benutzerfreundlich", „innovativ", „optimieren", „auf Ihre
   Bedürfnisse zugeschnitten"); keine erfundenen Zahlen oder Kundenstimmen;
   konkreter Nutzen statt Schlagwort; kein Preis in einer Erstansprache; der
   Text könnte so rausgehen.
2. Aufruf mit `force_model=TEXT_MODELL` (Groq 70B). Die Prüfung braucht keine
   Werkzeuge, deshalb darf die schnelle Stufe ran.
3. Antwort muss mit `FREIGABE` oder `REVISION` beginnen. Bei `REVISION` wird
   die Begründung als Anmerkung gespeichert **und** als Lehre gemerkt, damit
   derselbe Fehler nicht beim nächsten Lauf wiederkommt.
4. Scheitert die Prüfung selbst, wird im Zweifel freigegeben
   (Z. 561–567): „lieber legt Tino etwas Mittelmäßiges beiseite, als dass
   Arbeit unsichtbar liegen bleibt." Die Anmerkung lautet dann „Ungeprueft".

Der Redakteur wird von der Chefprüfung ausgenommen (Z. 440–441) — er legt
nichts vor.

### 5.4 Was Tino in der GUI sieht

- **Vier Ampeln im Cockpit** (`index.html:42–45`, Buttons `.toggle.rev` mit
  `data-art`): grün, sobald für diese Art etwas offen ist, sonst rot. Ein Klick
  springt in den System-Tab und scrollt zur Abnahmeliste (`app.js:99–100`).
- **Ticker-Feld „ABNAHME"** (`index.html:72`): zeigt „offen / bei Jude / in
  Revision" (`app.js:106–107`).
- **Abschnitt „Abnahme"** im System-Tab (`index.html:152–154`): Karten mit
  Auszug, Volltext-Knopf, Anmerkungsfeld und den Schaltflächen Abnehmen und
  Revision. Eine Revision ohne Anmerkung wird schon im Browser abgelehnt
  (`app.js:105`).

Endpunkte: `GET /api/reviews`, `GET /api/reviews/{id}`,
`POST /api/reviews/{id}/abnehmen|revision` (`web/app.py:662–694`).

### 5.5 Abgrenzung in einem Satz

| | Bestätigungen (`confirmations.py`) | Abnahme (`review.py`) |
|---|---|---|
| Was wartet | Eine **Aktion** vor der Ausführung | Ein **fertiges Erzeugnis** |
| Blockiert | Ja — das Werkzeug läuft nicht | Nein — der Mitarbeiter arbeitet weiter |
| Zwischeninstanz | keine | Jude prüft vor Tino |
| Auslöser | `confirm_action` am Werkzeug | `submit_for_review` |
| Tabelle | `confirmations` (zentrales Schema) | `reviews` (selbst angelegt) |
| Ausführung | `ActionExecutor` | keine — nichts wird versendet oder veröffentlicht |

---

## 6. Notion

Datei: `AGENT/services/notion_db.py` (203 Z.), zusätzlich
`AGENT/services/notion.py` (209 Z.) für die Rezeptdatenbank.

### 6.1 Angebundene Datenbanken

`DATABASES` (`notion_db.py:22–32`) ordnet Kurznamen den Umgebungsvariablen zu.
Der Kurzname ist das, was die Agenten angeben.

| Kurzname | Umgebungsvariable | in `.env` gesetzt |
|---|---|---|
| `kontakte` | `NOTION_DB_CONTACTS` | ja |
| `sequenzen` | `NOTION_DB_SEQUENCES` | ja |
| `mail_inhalte` | `NOTION_DB_CONTENT` | ja |
| `content_stuecke` | `NOTION_DB_CONTENT_PIECES` | ja |
| `scheduling` | `NOTION_DB_SCHEDULING` | ja |
| `social_posts` | `NOTION_DB_SOCIAL` | ja |
| `abonnenten` | `NOTION_DB_SUBSCRIBERS` | ja |
| `rezepte` | `NOTION_DB_RECIPES` | ja |
| `essensplan` | `NOTION_DB_MEALPLAN` | ja |

Dazu `NOTION_API_KEY`. Fehlt der Schlüssel, wirft `_headers()` (Z. 45–49) mit
einem klaren Text; fehlt eine Datenbank-ID, wirft `_database_id()` (Z. 51–59).
`databases()` (Z. 61–64) meldet ohne Netzzugriff, welche eingerichtet sind —
das ist der günstige Weg für die Agenten, sich zu orientieren.

API: `https://api.notion.com/v1`, Version `2022-06-28` (Z. 18–19).

### 6.2 Schemagetriebenes Arbeiten

Der Dienst schreibt keinen Code je Datenbank, sondern liest die Feldliste live
aus Notion. Neue Datenbanken brauchen nur einen Eintrag in `DATABASES`
(Z. 6–8).

`schema(name)` (Z. 66–89) holt `GET /databases/{id}` und liefert:
- `felder`: Feldname → Notion-Feldtyp,
- `erlaubte_werte`: für `select`, `status` und `multi_select` die gültigen
  Optionsnamen,
- `titel` der Datenbank.

Das Ergebnis wird je Prozess zwischengespeichert (`_schema_cache`, Z. 38).

Die Werkzeugbeschreibung von `notion_schema` sagt ausdrücklich: „Vor jedem
Schreiben aufrufen." (`tools/skills.py:173`). Der Grund steht in
`skills.py:169–170`: ohne das raten die Agenten Feldnamen und schreiben ins
Leere.

Umwandlung in beide Richtungen: `_plain` (Z. 93–113) macht aus einer
Notion-Eigenschaft einen einfachen Python-Wert, `_to_property` (Z. 115–142)
den umgekehrten Weg — passend zum Feldtyp, mit Kappung auf 2000 Zeichen bei
Text. Ein Feldtyp, der nicht geschrieben werden kann (z. B. `formula`), wirft.

### 6.3 Warum unbekannte Auswahlwerte abgewiesen werden

`_validate` (Z. 165–185) läuft vor jedem `create` und `update`. Zwei Prüfungen:

1. **Unbekannte Feldnamen** → Ausnahme mit der Liste der vorhandenen Felder.
2. **Unbekannte Auswahlwerte** → Ausnahme mit der Liste der erlaubten Werte.

Die Begründung im Code (Z. 168–171):

> Notion legt unbekannte Auswahlwerte stillschweigend als neue Option an —
> nach ein paar Agentenläufen wären die Listen unbrauchbar. Deshalb wird hier
> hart abgewiesen statt stillschweigend erweitert.

Ein Agent, der `Status='in Bearbeitung'` statt `'In Arbeit'` schreiben will,
bekommt also einen Fehler mit der korrekten Liste zurück — und nicht eine
neunte Statusoption in Tinos Board.

Leere Werte (`None`, `""`) werden von der Wertprüfung ausgenommen (Z. 178).

### 6.4 Rezepte

`services/notion.py` ist ein eigener, schmalerer Zugriff auf die
Küchenmanagement-Datenbank. Er braucht `NOTION_API_KEY` und
`NOTION_DB_RECIPES` und meldet sich als nicht eingerichtet, statt zu werfen
(`available()`, Z. 140–141) — das Cockpit zeigt dann einen Hinweis.

`recipes()` (Z. 179–200) blättert über `next_cursor` durch alle Seiten;
`today()` (Z. 202–209) wählt deterministisch über die Tagesnummer, damit das
Cockpit bei jedem Poll dasselbe Gericht zeigt.

---

## 7. Weitere Dienste

Alle unter `AGENT/services/`.

### 7.1 E-Mail — `mail.py` (193 Z.)

Fünf fest verdrahtete Konten (`ACCOUNTS`, Z. 18–24): `gmx`, `yahoo`, `gmail`,
`proton_nurovelle`, `proton_mongojude`. Die beiden Proton-Konten laufen über
die lokale Proton Bridge (`127.0.0.1`, IMAP 1143, SMTP 1025, STARTTLS ohne
Zertifikatsprüfung — die Bridge lauscht nur lokal und nutzt ein
selbstsigniertes Zertifikat, Z. 190).

Konfiguration je Konto über das Muster `MAIL_<PREFIX>_<SCHLÜSSEL>`
(`_env`, Z. 14–15): `USERNAME` (optional), `PASSWORD` (Pflicht),
`IMAP_HOST`, `SMTP_HOST`, `IMAP_PORT`, `SMTP_PORT`, `VERIFY_TLS`.

Lesend ohne Bestätigung: `search` (Z. 78), `read` (Z. 101, holt Text/Plain,
sonst HTML über BeautifulSoup), `create_draft` (Z. 122).
Bestätigungspflichtig: `archive` (Z. 135), `send_confirmed` (Z. 149),
`delete_confirmed` (Z. 164, verschiebt nach Möglichkeit in den Papierkorb).
Sonderordner werden aus den IMAP-Flags ermittelt (`_folders`, Z. 60–76).

### 7.2 Regenradar — `dwd_radar.py` (204 Z.) und `radar.py` (36 Z.)

**DWD** ist die erste Wahl. Quelle:
`https://opendata.dwd.de/weather/radar/composite/rv/` — das RV-Komposit,
deutschlandweit, 1 km Raster (DE1200, 1100 × 1200), 5-Minuten-Takt. Jeder Lauf
ist ein `tar.bz2` mit 25 RADOLAN-Binärdateien.

Die Dekodierung ist selbst geschrieben (`_decode`, Z. 68–76): ASCII-Header bis
ETX, danach little-endian uint16 je Zelle; Bit 13 markiert „außerhalb der
Abdeckung", die Wertbits 0–11 mal 0,01 ergeben mm je 5 min. Die
polarstereographische Projektion wird einmal vorberechnet (`_sample_index`,
Z. 50–65) und jeder Frame als RGBA-PNG in Web-Mercator-Ausrichtung gerendert
(`_render`, Z. 147–158), das die GUI als Leaflet-ImageOverlay über die feste
Bounding-Box legt.

Zeitraum: `PAST_RUNS = 18` × 5 min = **90 min Rückschau**,
`FORECAST_MAX_MIN = 90` = **90 min Vorhersage**. Die Rückschau-Läufe werden
parallel mit acht Threads geholt (Z. 118–131) — sequenziell kostete der
Kaltstart 15–30 s. `warmup()` (Z. 167–176) füllt den Cache beim Start im
Hintergrund. Neuabfrage frühestens alle 240 s (Z. 112).

Konfiguration: keine. Der Dienst braucht nur Netzzugang.

**RainViewer** (`radar.py`) ist der Notnagel. `web/app.py:428–438` versucht
zuerst DWD und fällt bei einer Ausnahme darauf zurück. Der Standort kommt aus
`RadarService` (Z. 11–16) und ist über `JUDE_RADAR_LAT`, `JUDE_RADAR_LON`,
`JUDE_RADAR_ZIP`, `JUDE_RADAR_CITY`, `JUDE_RADAR_ADDRESS`, `JUDE_RADAR_ZOOM`
anpassbar; Vorgabe ist Berliner Straße, 35039 Marburg.

### 7.3 Wetter — `weather.py` (65 Z.)

Open-Meteo, kostenlos und ohne Schlüssel. Nutzt die Radar-Koordinaten
(Z. 30–31) und cached 600 s (`CACHE_SECONDS`, Z. 27), damit das
Cockpit-Polling keine Dauerlast erzeugt. Liefert Temperatur, gefühlte
Temperatur, Feuchte, Wind, Zustandstext (Codetabelle Z. 15–23) sowie Tages-Min
und -Max.

Daneben gibt es das Werkzeug `get_current_weather` (`tools/wetter.py`) für
beliebige Orte — mit Geokodierung, die den Ortsnamen bei Misserfolg
schrittweise verkürzt (Z. 9–21).

### 7.4 Markt — `market.py` (135 Z.)

Vier Märkte (`MARKETS`, Z. 13–18), jeder mit einem ausdrücklichen Vorbehalt:

| Markt | Quelle | Vorbehalt |
|---|---|---|
| BTC/EUR | Binance `BTCEUR` | Binance Spot |
| BTC/USD | Binance `BTCUSDT` | USDT als USD-Näherung |
| XAU/USD | Yahoo `GC=F` | Gold-Futures, kein Spotpreis |
| XAU/EUR | `GC=F` / `EURUSD=X` | abgeleiteter Proxy |

4h-Kerzen gibt es bei Yahoo nicht und werden aus 1h aggregiert
(`_aggregate_four_hour`, Z. 95). Kerzen werden vor dem Speichern validiert
(`_validate_candles`, Z. 39) und in `candles` abgelegt. `csv_export` (Z. 129)
speist den CSV-Knopf in der GUI. Keine Konfiguration nötig.

### 7.5 ICT/SMC — `ict.py` (269 Z.) und `ict_training.py` (217 Z.)

Ausdrücklich eine **Demo-Analyse**, read-only. Der Datenzugriff läuft über
einen MT5-MCP-Client (`MT5MCPClient`, Z. 24), dessen Startbefehl aus
`MT5_MCP_COMMAND` kommt.

- `analyse_live(router, symbol)` (Z. 198) lädt H4, H1 und M1 gemeinsam und
  lässt das Modell eine Trading Card erzeugen; `_validate_card` (Z. 181)
  prüft sie, bevor sie in `trading_cards` landet.
- `scheduler_config` (Z. 100) liest die Kill Zones. Zeitzonen:
  `ICT_TIMEZONE` (Vorgabe `America/New_York`), `ICT_LOCAL_TIMEZONE` (Vorgabe
  `Europe/Berlin`); ein JSON-Override geht über `ICT_KILL_ZONES`.
  Ein-/Ausschalter: `ICT_SCHEDULER_ENABLED` (Vorgabe an).
- `run_due(router)` (Z. 227) wird vom Web-Scheduler jede Minute aufgerufen
  (`web/app.py:106–109`) und protokolliert in `scheduler_runs`.
- `ict_training.py` baut aus M1-Daten einen Merkmalsvektor
  (`FEATURE_NAMES`, Z. 15) und trainiert walk-forward; das Modell liegt unter
  `MODEL_DIR`.

Der Systemprompt für die Analyse wird aus `ICT_PROMPT_FILE` gelesen
(`core/paths.py:46`): `AI-Data/Projects/ICT_SNIPER/ict_trading_bot_systemprompt_1.txt`.

### 7.6 Dokumentwissen (RAG) — `documents.py` (124 Z.)

Vollständig lokal. Texte und PDFs werden in Abschnitte zerlegt, mit einem
Ollama-Embedding-Modell vektorisiert und in der Tabelle `rag_chunks`
gespeichert; die Suche bettet die Frage ein und sortiert per Kosinusähnlichkeit.

Konfiguration: `OLLAMA_URL` (Vorgabe `http://127.0.0.1:11434`),
`JUDE_EMBED_MODEL` (Vorgabe `nomic-embed-text` — auf diesem Rechner
installiert).

Die Abschnittsgröße ist **220 Wörter** mit 40 Wörtern Überlappung (Z. 60).
Der Kommentar (Z. 62–67) hält fest, warum: vorher standen dort 900 Wörter,
und `nomic-embed-text` brach mit „the input length exceeds the context
length" ab — jedes Einlesen scheiterte.

Aktueller Bestand: 3 Dokumente, 41 Abschnitte.

### 7.7 Bilder — `images.py`, `vision.py`, `render3d.py`, `ocr.py`

| Dienst | Aufgabe | Konfiguration |
|---|---|---|
| `images.py` (110 Z.) | Erzeugen und Bearbeiten über die OpenAI-Images-API; Ablage unter `Jude/images` mit Metadaten-JSON je Bild (Prompt, Größe, Modell, Zeit) | `OPENAI_API_KEY` (Pflicht), `JUDE_IMAGE_MODEL` (Vorgabe `gpt-image-1`) |
| `vision.py` (85 Z.) | Bilder verstehen; erst lokal über Ollama, sonst Cloud | `OLLAMA_URL`, `JUDE_VISION_MODEL`, `JUDE_VISION_CLOUD_MODEL`, `OPENAI_API_KEY` |
| `render3d.py` (204 Z.) | Lokales Rendern mit Blender (headless) | `BLENDER_BIN`; `JUDE_BLENDER_RAW` schaltet freie bpy-Skripte frei |
| `ocr.py` (45 Z.) | Text aus Bild oder PDF über Tesseract | Tesseract muss installiert sein (`/usr/bin/tesseract` vorhanden) |

`render3d.py` prüft bpy-Skripte statisch (`_assert_safe_bpy`, Z. 32) gegen
eine Import- und Namens-Whitelist. Der bevorzugte Weg ist die strukturierte
Objektliste (`render_spec`, Z. 200), nicht das freie Skript — freie Skripte
verlangen zusätzlich `JUDE_BLENDER_RAW=true` (Z. 106–108).

### 7.8 Sprache — `speech/`

| Datei | Aufgabe |
|---|---|
| `stt.py` (262 Z.) | Wake-Word, energiebasierte Aufnahme bis zur Sprechpause, Transkription über faster-whisper |
| `tts.py` (251 Z.) | Text → deutsche Ausspracheumschrift → Piper-CLI → Resampling → Wiedergabe, unterbrechbar |
| `wakeword.py` (59 Z.) | ONNX-Streaming-Klassifikator für das selbst trainierte Modell |
| `controller.py` (309 Z.) | Hintergrund-Thread für GUI/Desktop mit Ringpuffer für das Polling |

**Zwei Wake-Word-Wege** (`stt.py:186–191`), umschaltbar über
`JUDE_WAKE_ENGINE`:
- `whisper` (**Vorgabe**): `PhraseWakeListener` transkribiert überlappende
  Fenster und vergleicht den Wortlaut mit der Phrase. Braucht kein
  Trainingsmodell.
- `onnx`: `OnnxWakeWordListener` nutzt `jude_angetreten.onnx` als schnellen
  Vorfilter, den Whisper danach bestätigt (Z. 168–176).

**Modelldateien** — alle vorhanden und geprüft:

| Artefakt | Pfad | Größe |
|---|---|---|
| Whisper base | `Jude/models/whisper-base/model.bin` | 145 MB |
| Whisper small | `Jude/models/whisper-small/model.bin` | 483 MB |
| Piper Thorsten high | `Jude/models/piper/de_DE-thorsten-high.onnx` | 114 MB |
| Piper Thorsten medium | `Jude/models/piper/de_DE-thorsten-medium.onnx` | 63 MB |
| Wake-Word | `Jude/models/wakeword/jude_angetreten.onnx` (+ `.json`) | 820 KB |
| Piper-Binary | `.venv/bin/piper` (**nicht auf PATH**) | |

Die Metadaten des Wake-Word-Modells passen zum Code: `input_windows=16`,
`features=96` → 1536, das ONNX-Input hat Shape `[None, 1536]`.

Env: `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_LANGUAGE`,
`JUDE_WAKE_PHRASE`, `JUDE_WAKE_WINDOW`, `JUDE_WAKE_HOP`,
`WAKE_WORD_TRIGGER_FRAMES`, `JUDE_WAKE_ENGINE`, `VOICE_ENERGY_THRESHOLD`,
`WAKE_WORD_MODEL`, `WAKE_WORD_THRESHOLD`, `PIPER_MODEL`, `PIPER_SPEAKER`,
`JUDE_TTS_PITCH`, `JUDE_TTS_SPEED`, `JUDE_TTS_PAUSE`, `JUDE_TTS_RATE`,
`JUDE_TTS_HEADROOM`, `PIPER_NOISE_SCALE`, `PIPER_NOISE_W`,
`JUDE_GREETING`, `JUDE_FAREWELL`, `JUDE_BRIEFING`.

Der `VoiceController` (`controller.py:40`) hält einen 200er-Ringpuffer, den die
GUI über `/api/voice/events?since=…` abfragt. Er erkennt gesprochenes Lob und
Kritik und speist es als Routing-Feedback ein (`_maybe_feedback`, Z. 218). Das
volle Briefing gibt es einmal täglich; der Stand liegt in
`DATA_DIR/voice_briefing.json`.

**Wichtig:** Die gesamte Sprachschicht läuft ausschließlich über den
venv-Interpreter. Piper wird über den Geschwisterpfad von `sys.executable`
gefunden (`tts.py:50–51`), und im System-Python fehlen `sounddevice`,
`faster_whisper`, `onnxruntime` und `pyopen_wakeword`.

### 7.9 Zeitsteuerung — `scheduler.py` (198 Z.)

Aufgaben liegen als JSON in `DATA_DIR/scheduled_tasks.json`. Drei Arten
(`create`, Z. 45–71): `prompt` (eine Anfrage an Jude selbst), `briefing`
(gesprochenes Kurzbriefing), `tool` (Werkzeugaufruf). Zeitplan entweder
täglich um `HH:MM` oder alle `every_minutes` Minuten.

`tick()` (Z. 148–169) wird vom Web-Prozess alle 30 s aufgerufen
(`web/app.py:112–117`), und zwar unter demselben `chat_lock` wie der Chat.
Jedes Ergebnis wird als Benachrichtigung abgelegt und bei laufender
Sprachsteuerung vorgelesen.

Zwei Korrekturen sind im Code dokumentiert:
- `_lokal` (Z. 97–114) rechnet `last_run` (UTC) in die Ortszeit um. Ohne das
  galt eine Aufgabe um 00:30 Ortszeit den ganzen Vormittag als nicht gelaufen
  und feuerte alle 30 Sekunden erneut.
- `_abschluss` (Z. 171–198) trägt die **Endzeit** ein, nicht die Startzeit des
  Ticks, und legt bei einem Fehlschlag einen Nachholtermin nach 30 Minuten an
  (`WIEDERHOLUNG_MIN`, `MAX_VERSUCHE = 2`).

Aktuell ist genau eine Aufgabe angelegt: „Nächtliche Sicherung" um 03:00.

### 7.10 Sicherungen — `backup.py` (83 Z.)

Ziel: `JUDE_DIR/backups/jude-backup-JJJJMMTT-HHMMSS.zip`. Inhalt: eine
konsistente Kopie der SQLite-Datenbank über die Online-Backup-API
(`_consistent_db_copy`, Z. 28–38) plus `models.yaml`, `sub_agents.json`,
`scheduled_tasks.json` und `voice_briefing.json`.

**Die `.env` ist bewusst nicht dabei** (Z. 5–6): die ZIPs liegen auf NTFS und
sollen keine Schlüssel enthalten. Es werden 14 Sicherungen behalten
(`keep`, Z. 25). Eine automatische Wiederherstellung gibt es nicht — nur einen
Hinweistext (`restore_info`, Z. 80–83).

Vorhanden sind 13 Archive vom 04.08. bis 13.08.

### 7.11 Übrige Dienste in Kürze

| Datei | Aufgabe | Konfiguration |
|---|---|---|
| `memory.py` (252 Z.) | Langzeitgedächtnis mit Kandidaten/bestätigt, Trainingsausschlüssen und episodischem Rückruf | `JUDE_MEMORY_AUTOAPPROVE` (Vorgabe 0.8) |
| `briefing.py` (261 Z.) | Kurzbriefing für Sprache und GUI: Marktlage, ICT-Ablesung, Schlagzeilen je Thema aus Google News | `JUDE_BRIEFING_ICT_LIVE` |
| `news.py` (77 Z.) | Nachrichten in drei Bereichen (Weltlage 8, Crypto 4, Tech 4) über **newsdata.io**, gefiltert gegen Kursmeldungen, plus fertiger Journalisten-Prompt | `NEWS_API_KEY` |
| `scraper.py` (174 Z.) | Öffentliche URLs extrahieren mit robots.txt-, Größen-, Redirect- und Privatnetz-Schutz; YouTube über yt-dlp | – |
| `fact_checker.py` (148 Z.) | Prüft Behauptungen gegen unabhängige Quellen; bestätigt erst ab zwei zusätzlichen | `FACT_CHECK_MAX_CLAIMS` |
| `home_assistant.py` (112 Z.) | Licht schalten, Grow-Sensoren lesen, Allowlist-Aktionen ausführen | `HOME_ASSISTANT_URL`, `HOME_ASSISTANT_TOKEN`, `HA_LIGHT_*`, `HA_GROW_SENSORS_JSON`, `HA_ALEXA_ACTIONS_JSON`, `HA_GROW_ACTIONS_JSON` |
| `coding.py` (131 Z.) | Git-Operationen und Testläufe auf Repositories unter AI-Data | – |
| `filesystem.py` (77 Z.) | Pfadauflösung mit Sperrliste; direktes Schreiben nur unter AI-Data | – |
| `remote.py` (68 Z.) | SSH/SCP zu freigegebenen Hosts, schlüsselbasiert (`BatchMode=yes`) | `JUDE_SSH_HOSTS`, sonst `~/.ssh/config` |
| `system_monitor.py` (129 Z.) | CPU, RAM, Platte, Netz, Temperaturen für die Cockpit-Instrumente | – |
| `health.py` (104 Z.) | Ollama, Modelle, Platte, DB, Mikrofon, Wake-Word-Modell, Budget, letzte Fehler | `OLLAMA_URL` |
| `notifications.py` (40 Z.) | Benachrichtigungen anlegen, auflisten, als gelesen markieren | – |
| `calendar.py` (46 Z.) | Termine als ICS unter `AI-Data/Kalender` ablegen | – |
| `meals.py` (257 Z.) | Low-Carb-Essensplan mit Validierung, Einkaufsliste und PDF | – |
| `shopping.py` (169 Z.) | Preisvergleich G-Star/Nike über offizielle Produktdaten; keine Bestellung | – |
| `system`-Grenzen | `filesystem.BLOCKED_PARTS`: `$RECYCLE.BIN`, `System Volume Information`, `.Trash-1000` | – |

---

## 8. Web und GUI

### 8.1 Server — `AGENT/web/app.py` (694 Z.)

FastAPI-Anwendung. `build_application()` wird auf Modulebene aufgerufen
(Z. 56); alle Dienste werden als Modulvariablen instanziiert (Z. 58–83).

**Authentifizierung** (`require_auth`, Z. 90–103), global als
`dependencies=[Depends(require_auth)]` am `FastAPI(...)` verankert (Z. 150):

1. Lokaler Client **und** weder `JUDE_GUI_USER` noch `JUDE_GUI_PASSWORD`
   gesetzt → durchlassen (Z. 92–93).
2. Nicht lokal und keine Zugangsdaten gesetzt → **403**, „Remote-Zugriff ist
   ohne GUI-Zugangsdaten gesperrt."
3. Sonst HTTP Basic Auth, verglichen mit `hmac.compare_digest` (Z. 102).

Das Frontend tut dafür nichts — den Dialog übernimmt der Browser.

**Origin-Guard** (`_origin_guard`, Z. 153–169): Bei allen Methoden außer GET,
HEAD und OPTIONS werden fremde `Host`- und `Origin`-Header mit 403 abgewiesen.
Erlaubt sind `127.0.0.1`, `localhost`, `::1`, der Wert von `JUDE_HOST` und die
Komma-Liste in `JUDE_ALLOWED_HOSTS` (Z. 136–140). Das schützt gegen CSRF und
DNS-Rebinding. Dieselbe Middleware setzt `Cache-Control: no-cache` für `/` und
`/static`.

**Hintergrundschleifen** (`lifespan`, Z. 120–133): DWD-Radar-Warmup,
ICT-Kill-Zone-Schleife (jede Minute, nur wenn der Scheduler aktiviert ist),
Aufgaben-Scheduler (alle 30 s), und bei `JUDE_VOICE` der Sprach-Thread.

Ein globaler Exception-Handler (Z. 177–179) gibt jede unbehandelte Ausnahme
als `{"error": …}` mit Status 500 zurück.

### 8.2 Endpunkte nach Bereich

| Bereich | Endpunkte |
|---|---|
| Basis | `GET /`, `GET /api/status`, `GET /api/health`, `GET /api/system` |
| Chat & Routing | `POST /api/chat`, `POST /api/routing/{route_id}/feedback` |
| Sprache | `GET /api/voice`, `GET /api/voice/events`, `POST /api/voice/start`, `/stop`, `/phrases`, `/skip`, `/skip-all` |
| Gedächtnis | `GET /api/memory`, `POST /api/memory`, `POST /api/memory/{id}/approve`, `DELETE /api/memory/{id}` |
| Wissen (RAG) | `GET /api/documents`, `POST /api/documents/ingest`, `POST /api/documents/search`, `DELETE /api/documents` |
| Team | `GET /api/agents`, `POST /api/agents`, `DELETE /api/agents/{name}`, `POST /api/agents/{name}/run` |
| Bestätigungen | `GET /api/confirmations`, `POST /api/confirmations`, `POST /api/confirmations/{id}/{approve\|reject}` |
| Abnahme | `GET /api/reviews`, `GET /api/reviews/{id}`, `POST /api/reviews/{id}/{abnehmen\|revision}` |
| Aufgaben & Sicherung | `GET/POST /api/tasks`, `DELETE /api/tasks/{id}`, `GET/POST /api/backups` |
| Markt & ICT | `GET /api/market/{markt}`, `GET /api/market/{markt}/csv`, `GET /api/ict/status`, `/cards`, `POST /api/ict/analyse/{symbol}`, `POST /api/ict/train/{symbol}` |
| Wetter & Radar | `GET /api/weather`, `GET /api/radar`, `GET /api/radar/frame/{key}.png` |
| Nachrichten | `GET /api/news`, `GET /api/news/brief`, `GET /api/briefing` |
| E-Mail | `GET /api/mail/status`, `GET /api/mail/{konto}/search`, `GET /api/mail/{konto}/{id}`, `POST /api/mail/draft`, `POST /api/mail/archive` |
| Code | `GET /api/coding/repositories`, `POST /api/coding/{status\|test\|read\|write\|diff\|branch\|commit\|push\|pr}` |
| Bilder | `GET /api/images`, `POST /api/images/generate`, `/edit`, `/render3d`, `POST /api/vision`, `POST /api/ocr` |
| Haus | `GET /api/lights`, `POST /api/lights/{raum}/{zustand}`, `GET /api/home-actions`, `POST /api/home-actions/{gruppe}/{aktion}`, `GET /api/grow` |
| Alltag | `GET /api/calendar`, `GET /api/calendar/{id}/ics`, `GET /api/recipes/today`, `GET /api/meals/current`, `POST /api/meals`, `GET /api/meals/{id}/pdf`, `POST /api/shopping` |
| Sonstiges | `POST /api/scrape`, `POST /api/fact-check`, `GET /api/notifications`, `POST /api/notifications/{id}/read` |
| Statisch | `/static/*`, `/images/*` |

Zwei Endpunkte prüfen ausdrücklich den Zielpfad, bevor sie eine Datei
ausliefern: der Essensplan-PDF-Download muss unterhalb von `MEALS_DIR` liegen
(Z. 601–605), und die ICS-ID muss alphanumerisch sein (Z. 611–612).

### 8.3 Oberfläche — `web/static/`

Drei Dateien: `index.html` (156 Z.), `app.js` (232 Z., dicht geschrieben),
`app.css` (465 Z.). Leaflet liegt lokal unter `static/vendor/`.

**Kopfleiste** (`index.html:8`): Wetter links, Uhr in der Mitte,
Token-/Budgetanzeige rechts, dazu Sprachsteuerung ein/aus, „Thema
überspringen" und zwei Zustandsabzeichen.

**Vier Tabs.** Die Navigation ist im HTML leer und wird in `app.js:2–3` aus
den `<section data-page="…">` erzeugt:

| Tab | Zeilen | Inhalt |
|---|---|---|
| **Cockpit** | 10–74 | Kachelraster und Ticker — die Startseite |
| **Märkte** | 75–80 | OHLCV-Chart, ICT/SMC, Kill Zones, Trading Cards |
| **Werkzeuge** | 81–107 | News, Postfächer, Coding, Shopping, Essensplan, Bilder, 3D |
| **System** | 108–156 | Team, Zustand, Aufgaben, Sicherungen, Wissen, Gedächtnis, Routing, Abnahme, Bestätigungen |

Beim Tabwechsel werden bereichsabhängig Daten nachgeladen (`show(p)`,
`app.js:2–5`).

**Cockpit-Raster.** Im HTML sind die Kacheln in vier Spaltencontainer
gruppiert (`.cockL`, `.cockC`, `.cockR`, `.cockD`). Das CSS löst diese
Container ab `app.css:127` per `display:contents` wieder auf — das tatsächliche
Layout ist ein `auto-fill`-Raster mit `minmax(196px, 1fr)`, in dem einzelne
Kacheln über `grid-column: span N` mehr Platz bekommen (die Ausgabespalte
z. B. 3 Spalten und 4 Zeilen).

Kacheln:

| Kachel | Zeile | Inhalt |
|---|---|---|
| Regenradar | 13–16 | Leaflet-Karte, Zeitleiste (`#radarFrame`), Ortsangabe |
| Growcontroller | 17 | `#hudGrow` |
| Fake-Checker | 18–21 | URL-Feld, Ergebnis |
| Systemband | 24–48 | 7 Rundinstrumente, 4 Schalter, 4 Abnahme-Ampeln |
| Aktivierungswort | 49–57 | Wake-/Schlafwort, Begrüßung, Verabschiedung |
| Ausgabe (Chat) | 60 | `#chat`, `#chatForm` |
| Kalender | 63 | Termin vormerken, Terminliste |

Die sieben Instrumente: CPU, RAM, Platte, Netz, CPU-Temperatur,
GPU-Temperatur, NVMe-Temperatur (`#gCpu` … `#gNvme`).
Die vier Schalter (`#tglVoice`, `#tglMarket`, `#tglNews`, `#tglRadar`) merken
sich ihren Zustand in `localStorage` unter `judeToggles` — nur die Stimme wird
serverseitig geführt.
Die Ticker-Leiste unten (66–73) zeigt News, XAU/USD, BTC/USD, Modell, Stimme
und Abnahme.

**Wichtigste JS-Funktionen:**

| Funktion | Zeile | Aufgabe |
|---|---|---|
| `api(url, opt)` | 8 | Zentraler fetch-Wrapper, setzt nur `Content-Type` |
| `#chatForm.onsubmit` | 10–19 | Chat senden; zeigt „Jude denkt …" als Platzhalter, weil Antworten 16–60 s dauern; hängt danach Modellname und Feedback-Knöpfe an |
| `pollVoice()` | 117 | Long-Poll auf `/api/voice/events`; 1,2 s wenn laufend, sonst 4 s |
| `voiceBadge(s)` | 116 | Zustände aus/wartet/aufnahme/denkt/spricht/aktiv/fehler |
| `pollSystem()` | 125–133 | Instrumente, alle 2,5 s |
| `setGauge(...)` | 124 | Setzt CSS-Variable `--p` und die Klassen `warn`/`hot` |
| `loadRadar()` | 57–75 | Leaflet-Karte, OSM-Basiskarte, Marker, `ResizeObserver` gegen Layout-Verrutschen |
| `showRadarFrame(i)` | 51–56 | DWD als `imageOverlay` über `/api/radar/frame/{key}.png`, RainViewer als Kachelebene |
| `loadReviews()` | 101 | Abnahmekarten mit Auszug und Volltext-Knopf |
| `reviewDecide(id, was)` | 105 | Abnehmen/Revision; Revision ohne Anmerkung wird schon hier blockiert |
| `ampeln(nachArt)` | 103 | Setzt die vier Ampeln |
| `loadAgents()` | 209–211 | Team laden, Skill-Raster füllen |
| `runAgent(name, btn)` | 213 | `POST /api/agents/{name}/run` |
| `requestConfirmation(...)` | 112 | Mail senden/löschen und Termine als Bestätigung vormerken |
| `loadConfirmations()` | 98 | Bestätigungsliste; `decide(id, d)` entscheidet |
| `drawCandles(rows)` | 20–42 | Candlestick-Chart auf Canvas |

Polling-Intervalle: Instrumente 2,5 s · Sprache 1,2/4 s · Tokenanzeige 15 s ·
Abnahme und Grow 60 s · Kursticker 120 s · Radar 300 s · Wetter und News 600 s ·
Essensplan/Rezept 900 s. Alle Schleifen starten in `app.js:188`.

**Desktop-Fenster** (`web/desktop.py`, 57 Z.): startet uvicorn in einem
Daemon-Thread und öffnet danach entweder ein pywebview-Fenster (1280 × 850)
oder — wenn kein WebView-Backend importierbar ist — ein Browser-App-Fenster.

---

## 9. Konfiguration

Alle Werte stehen in `AGENT/.env` (Modus 600, gitignoriert). Vorlage ist
`AGENT/.env.example` — die aber teilweise falsche Namen führt, siehe
[Auffälligkeiten](#12-auffälligkeiten).

Legende: **P** = Pflicht für den Grundbetrieb, **F** = Pflicht für die
jeweilige Funktion, **O** = optional mit sinnvoller Vorgabe.

### 9.1 Modelle und Kosten

| Variable | | Zweck |
|---|---|---|
| `OPENAI_API_KEY` | F | OpenAI-Modelle, Bilderzeugung, Cloud-Vision |
| `ANTHROPIC_API_KEY` | F | Claude-Stufen der Fallback-Kette |
| `GOOGLE_API_KEY` | F | Gemini (derzeit nicht in der Kette) |
| `GROQ_API_KEY` | F | Groq-Stufe; Judes Chefprüfung und der Redakteur |
| `DEEPSEEK_API_KEY` | F | DeepSeek-Stufe — **derzeit nicht gesetzt** |
| `OPENROUTER_API_KEY` | F | Unzensierte Cloud-Reserve — **derzeit nicht gesetzt** |
| `JUDE_PAID_MODELS_ENABLED` | O | Schaltet alle bezahlten Anbieter ab (Vorgabe `true`) |
| `JUDE_CLOUD_BUDGET_USD` | O | Monatsbudget (Vorgabe 5,00) |
| `JUDE_CLOUD_REQUEST_LIMIT_USD` | O | Grenze je Anfrage (Vorgabe 1,00) |
| `OPENAI_SERVICE_TIER`, `ANTHROPIC_SERVICE_TIER` | O | Tarifstufe erzwingen |
| `OLLAMA_URL` | O | Ollama-Adresse für RAG, Vision, Health (Vorgabe `http://127.0.0.1:11434`) |

### 9.2 Pfade und Grundbetrieb

| Variable | | Zweck |
|---|---|---|
| `AI_DATA_ROOT` / `JUDE_DATA_ROOT` | O | Datenwurzel überschreiben |
| `JUDE_DATA_DIR` | O | Datenverzeichnis überschreiben |
| `JUDE_GENERATED_TOOLS_DIR` | O | Ablage selbst erzeugter Werkzeuge |
| `JUDE_HOST`, `JUDE_PORT` | O | Bindeadresse und Port (Vorgabe 127.0.0.1:8765) |
| `JUDE_GUI_USER`, `JUDE_GUI_PASSWORD` | **F** | **Pflicht für jeden nicht-lokalen Zugriff** |
| `JUDE_ALLOWED_HOSTS` | O | Zusätzliche erlaubte Host-Header (Komma-Liste) |
| `JUDE_USER_NAME` | O | Anrede (Vorgabe „Tino") |
| `JUDE_PROFILE` | O | Profilfakten, per Semikolon getrennt |
| `JUDE_PRIVILEGED` | O | `false` sperrt bestätigte Systembefehle ganz |
| `JUDE_SANDBOX_IMAGE` | O | Docker-Image für den Tool-Sandbox-Test |
| `JUDE_MEMORY_AUTOAPPROVE` | O | Schwelle für automatisch bestätigte Erinnerungen (0.8) |

### 9.3 Sprache

| Variable | | Zweck |
|---|---|---|
| `JUDE_VOICE` | O | Sprach-Thread im GUI-Betrieb starten |
| `JUDE_WAKE_PHRASE`, `JUDE_SLEEP_PHRASE` | O | Aktivierungs- und Schlafkommando |
| `JUDE_WAKE_ENGINE` | O | `whisper` (Vorgabe) oder `onnx` |
| `JUDE_WAKE_WINDOW`, `JUDE_WAKE_HOP` | O | Fensterlänge und Schrittweite |
| `WAKE_WORD_MODEL`, `WAKE_WORD_THRESHOLD`, `WAKE_WORD_TRIGGER_FRAMES` | O | ONNX-Pfad, Schwelle, nötige Folgetreffer |
| `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_LANGUAGE` | O | STT-Modell, Gerät, Sprache |
| `VOICE_ENERGY_THRESHOLD` | O | Aufnahmeschwelle |
| `PIPER_MODEL`, `PIPER_SPEAKER`, `PIPER_NOISE_SCALE`, `PIPER_NOISE_W` | O | Stimme |
| `JUDE_TTS_PITCH`, `JUDE_TTS_SPEED`, `JUDE_TTS_RATE`, `JUDE_TTS_PAUSE`, `JUDE_TTS_HEADROOM` | O | Klang |
| `JUDE_GREETING`, `JUDE_FAREWELL` | O | Begrüßung und Verabschiedung |
| `JUDE_BRIEFING`, `JUDE_BRIEFING_ICT_LIVE` | O | Sprachbriefing an/aus, ICT-Livewerte |

### 9.4 Notion

`NOTION_API_KEY` (F) sowie `NOTION_DB_CONTACTS`, `NOTION_DB_SEQUENCES`,
`NOTION_DB_CONTENT`, `NOTION_DB_CONTENT_PIECES`, `NOTION_DB_SCHEDULING`,
`NOTION_DB_SOCIAL`, `NOTION_DB_SUBSCRIBERS`, `NOTION_DB_RECIPES`,
`NOTION_DB_MEALPLAN` — jeweils F für die betreffende Datenbank.

### 9.5 E-Mail

Je Konto `MAIL_<PREFIX>_PASSWORD` (F) und optional `MAIL_<PREFIX>_USERNAME`.
Präfixe: `GMX`, `YAHOO`, `GMAIL`, `PROTON_NUROVELLE`, `PROTON_MONGOJUDE`.
Für die Proton-Konten zusätzlich `_IMAP_PORT`, `_SMTP_PORT`, `_VERIFY_TLS` (O).

### 9.6 Haus, Markt, übrige Dienste

| Variable | | Zweck |
|---|---|---|
| `HOME_ASSISTANT_URL`, `HOME_ASSISTANT_TOKEN` | F | Home Assistant |
| `HA_LIGHT_WOHNZIMMER`, `HA_LIGHT_SCHLAFZIMMER`, `HA_LIGHT_FLUR` | O | Entity-IDs |
| `HA_ALEXA_ACTIONS_JSON`, `HA_GROW_ACTIONS_JSON` | O | JSON-Allowlisten |
| `HA_GROW_SENSORS_JSON` | O | Sensoren fürs Cockpit |
| `ICT_SCHEDULER_ENABLED`, `ICT_TIMEZONE`, `ICT_LOCAL_TIMEZONE`, `ICT_KILL_ZONES` | O | Kill-Zone-Steuerung |
| `MT5_MCP_COMMAND` | F | Startbefehl des MT5-MCP-Servers |
| `NEWS_API_KEY` | F | Crypto-News |
| `FACT_CHECK_MAX_CLAIMS` | O | Anzahl geprüfter Behauptungen |
| `JUDE_RADAR_LAT`, `_LON`, `_ZIP`, `_CITY`, `_ADDRESS`, `_ZOOM` | O | Standort (Vorgabe Marburg) |
| `JUDE_EMBED_MODEL` | O | Embedding-Modell für RAG |
| `JUDE_IMAGE_MODEL`, `JUDE_VISION_MODEL`, `JUDE_VISION_CLOUD_MODEL` | O | Bildmodelle |
| `BLENDER_BIN`, `JUDE_BLENDER_RAW` | O | Blender-Binary, freie bpy-Skripte |
| `JUDE_SSH_HOSTS` | O | Freigegebene SSH-Hosts (sonst `~/.ssh/config`) |

### 9.7 In der `.env` vorhanden, aber im Code nicht verwendet

Diese Schlüssel werden von keinem Modul gelesen. Sie stammen aus früheren
Ausbaustufen oder Planungen:

`CLOUDFLARE_TOKEN`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`,
`FIRECRAWL_API_KEY`, `GOOGLE_AI_STUDIO_KEY`, `NEWSDATAIO_API_KEY`,
`NOTION_PAGE_ID`, `OAUTH_ID`, `OAUTH_CLIENT_KEY`, `OLLAMA_API_KEY`,
`OLLAMA_DEVICE_KEY_BASE`, `PLUNK_PUBLIC_KEY`, `PLUNK_SECRET_KEY`,
`RESEND_API_KEY`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USER`, `EMAIL_PASS`,
`EMAIL_FROM`, `EMAIL_USE_TLS`, `EMAIL_DAILY_LIMIT`, `MT5_LOGIN`,
`MT5_PASSWORD`, `MT5_SERVER`.

---

## 10. Betrieb

### 10.1 Skripte in `AGENT/scripts/`

Gemeinsames Muster der neueren Skripte: `.env` laden, **Trockenlauf als
Standard**, Schreiben nur mit `--apply`.

| Skript | Zweck | Trockenlauf | Argumente |
|---|---|---|---|
| `team_setup.py` (338 Z.) | Schreibt die Arbeitsanweisungen aller neun Mitarbeiter nach dem Schema QUELLE/ARBEIT/ZIEL/FERTIG/NICHT. Validiert vorher jeden Skill gegen die Registry. | ja, `--apply` | `--apply` |
| `notion_migrate.py` (303 Z.) | Idempotente, rein additive Schema-Migration: fehlende Felder und Auswahloptionen ergänzen, irrtümlich angelegte Felder zurückbauen (nur wenn überall leer). | ja, `--apply` | `--apply` |
| `notion_nurovelle.py` (232 Z.) | Markenumstellung Autonova → Nurovelle plus Lead-Bereinigung in fünf Schritten. Prüft nach jedem PATCH, ob Optionen verloren gingen. | ja, `--apply` | `--apply`, `--nur {kanal,umstellen,termine,leads,adresstyp}` (mehrfach) |
| `import_prospects.py` (209 Z.) | Übernimmt die Akquise-Tracker aus `AI-Data/Projects/company/outreach` nach Notion `kontakte`. Idempotent über den Firmennamen. | ja, `--apply` | `--apply`, `--limit N` |
| `dienstplan_entfernen.py` (69 Z.) | Löscht aus `scheduled_tasks.json` ausschließlich Einträge mit `tool == "delegate_to_agent"`; Systemaufgaben bleiben. Legt vorher eine Kopie `.vor-entfernung` an. | ja, `--apply` | `--apply` |
| `training_export.py` (153 Z.) | Exportiert Gespräche als JSONL. Schließt aus: gesperrte Runden, negativ bewertete, zu kurze, Dubletten und alles mit Schlüsselmustern (`sk-`, `ntn_`, `gsk_`, `AIza`, `xox…`) — es wird nicht geschwärzt, die ganze Runde fliegt raus. | ja, aber Flag heißt **`--write`** | `--write`, `--out DATEI` |
| `train_wakeword.py` (250 Z.) | Aktuelles Wake-Word-Training. Datei-Level-Validierung plus harte Schranke: unter 90 % Recall oder über 1 % Falsch-Positive wird **kein Modell geschrieben**. | nein | `--data`, `--output` (beide Pflicht), `--name`, `--phrase`, `--cache` |
| `train_hey_jude.py` (176 Z.) | Ältere Fassung desselben Trainings, ohne Datei-Validierung und ohne Qualitätsschranke. | nein | wie oben |
| `split_wakeword.py` (122 Z.) | Zerlegt eine lange WAV-Aufnahme in Einzelclips (16 kHz Mono PCM16). | nein | `--input`, `--output-dir` (Pflicht), `--mode {silence,fixed}` u. a. |
| `train_my_wakeword.sh` (49 Z.) | Die Kette drumherum: Clips schneiden, Mindestmengen prüfen (≥15 positiv, ≥30 negativ), trainieren, altes Modell nach `*.bak-…` sichern, neues einsetzen. | nein | `$1` = Positiv-WAV, `$2` = Negativ-WAV |
| `train_ict.py` (29 Z.) | Walk-forward-Training des ICT-Gates. Lädt `.env` **nicht** selbst. | nein | `--symbol {XAUUSD,BTCUSD}` (Pflicht), `--csv`, `--count` |

### 10.2 Tests

```bash
cd /media/d4sd1ng/AI-Data/Projects/Jude/AGENT
../.venv/bin/python -m pytest
```

**77 Tests, keine Sammelfehler** (geprüft mit `--collect-only`).

| Datei | Tests | Schwerpunkt |
|---|---|---|
| `test_betrieb.py` | 27 | Laufbewertung, Agentengedächtnis, Abnahmekette Jude→Tino, Scheduler-Fälligkeit inkl. Mitternacht und Wiederholung, Lehren-Aggregation |
| `test_core.py` | 15 | ToolRegistry, Werkzeugschleife, Komplexitätsschätzung, Pfad-Escape, Modellauswahl, alle Adapter-Zuordnungen, Feedback nach drei Bewertungen |
| `test_completeness.py` | 14 | Werkzeugregistrierung, Bestätigungen, Fakten-Prüfer, Home Assistant, echtes OCR, echte ICS, echtes Git-Repo, FastAPI-Auth |
| `test_new_features.py` | 12 | Scheduler-CRUD, Sicherung und Rotation, episodisches Gedächtnis, RAG, Health, Sub-Agenten, SSH-Allowlist |
| `test_services.py` | 8 | Schreibgrenzen, Kill Zones, Sommerzeit, Binance-Zuordnung, OHLC-Validierung, Scraper-SSRF |
| `test_meals.py` | 1 | Kuratierter Fallback-Plan |

`conftest.py:11–21` legt je Test eine eigene SQLite-Datei unter
`TEST_DATA_DIR` an. **Achtung:** Einige Tests schreiben trotzdem echte Daten —
ein Git-Repo unter `TEST_REPOS_DIR`, eine PDF unter `MEALS_DIR`, und in
`test_core.py:175/195` Zeilen in die produktiven Tabellen `route_decisions`
und `model_usage`, weil `connection()` dort vor dem Patchen aufgelöst wird.
Alles mit `try/finally`-Aufräumen, aber es sind Schreibvorgänge.

Externe Voraussetzungen, alle erfüllt: `tesseract`, `DejaVuSans.ttf`, `git`,
die Verzeichnisse `Jude/test-data` und `Jude/test-repos`.

Zusätzlich sind `ruff` und `bandit` in `requirements.txt` gepinnt.

### 10.3 Logdatei

`/media/d4sd1ng/AI-Data/Jude/data/logs/jude.log`, rotierend (5 MB, 5
Sicherungen). Format: `Zeit STUFE modul: Nachricht`. Auf dem Bildschirm
erscheinen nur Warnungen und Fehler.

```bash
tail -f /media/d4sd1ng/AI-Data/Jude/data/logs/jude.log
grep -E 'WARNING|ERROR' /media/d4sd1ng/AI-Data/Jude/data/logs/jude.log | tail -50
```

Daneben liegt `start.out` mit der Ausgabe des Startskripts.

### 10.4 Jude neu starten

```bash
# laufenden Prozess finden und beenden
pgrep -af "main.py --gui"
kill <PID>

# neu starten (öffnet auch das Cockpit-Fenster)
/media/d4sd1ng/AI-Data/Projects/Jude/start.sh
```

`start.sh` erkennt einen bereits laufenden Server über `curl` auf den Port und
öffnet dann nur das Fenster. Für einen Start ohne Fenster:

```bash
cd /media/d4sd1ng/AI-Data/Projects/Jude/AGENT
../.venv/bin/python main.py --gui --host 127.0.0.1 --port 8765 --voice
```

Immer mit dem venv-Interpreter — sonst fehlen Whisper, Piper und die
Audio-Bibliotheken.

Ollama muss laufen (`systemctl status ollama` bzw. `pgrep -af "ollama serve"`).
Installiert und für Jude relevant sind: `qwen3:8b`, `dolphin3:8b`,
`nomic-embed-text` (RAG) und `qwen2.5vl:7b` (Vision).

---

## 11. Bekannte Einschränkungen

Alles hier ist am Code oder an den Daten belegt, nicht vermutet.

### 11.1 Zwei Fallback-Stufen sind tot

`DEEPSEEK_API_KEY` und `OPENROUTER_API_KEY` stehen **nicht** in `AGENT/.env`.
Damit fällt `cloud_deepseek_chat` (die günstige Zwischenstufe) und
`cloud_openrouter_dolphin` (die unzensierte Cloud-Reserve) aus der Kette. Die
Eskalation springt von Groq direkt auf `gpt-5.6-terra`, also von kostenfrei auf
bezahlt. Im unzensierten Pfad gibt es überhaupt keine Cloud-Stufe.

### 11.2 Gemini ist konfiguriert, aber nicht in der Kette

`models.yaml:153–155` hält fest, dass der Google-Schlüssel keinen API-Zugriff
hat. `cloud_gemini_flash` bleibt als Definition erhalten, steht aber in keiner
Fallback-Stufe.

### 11.3 Die Abnahme hat noch nie gelaufen

Die Tabelle `reviews` hat **0 Zeilen**. Kein Mitarbeiter hat bisher
`submit_for_review` benutzt, also gab es weder eine Chefprüfung noch eine
Abnahme durch Tino. Die Kette ist durch 27 Tests in `test_betrieb.py`
abgedeckt, aber im Betrieb unerprobt.

### 11.4 Eine Revision kommt nie aus dem Revisionszustand heraus

`ReviewQueue.erledigt()` (`services/review.py:129–134`) ist die einzige
Methode, die eine Vorlage von `revision` zurück auf `offen` setzt. Sie wird
**nirgends im Produktivcode aufgerufen** — nur in `test_betrieb.py:124`.

Praktisch heißt das: Legt ein Mitarbeiter nach einer Revision etwas Neues vor,
entsteht ein zweiter Eintrag, während der alte dauerhaft auf `revision` stehen
bleibt. `offene_revisionen()` liefert ihn deshalb bei **jedem** weiteren Lauf,
und der Mitarbeiter bekommt bis in alle Ewigkeit „ZUERST ERLEDIGEN" für eine
längst erledigte Anmerkung in den Prompt.

### 11.5 Neun Bestätigungen liegen unentschieden

In `confirmations` stehen 9 Einträge auf `pending`: 3 × `ssh_command`,
2 × `create_agent`, 2 × `code_push`, je 1 × `code_clone` und `code_pull`.
Sie werden nie verfallen — es gibt keinen Ablaufmechanismus. Von den beiden
entschiedenen `shell_command`-Einträgen ist einer fehlgeschlagen.

### 11.6 Das Wake-Word-Modell wird im Betrieb gar nicht benutzt

`JUDE_WAKE_ENGINE` steht nicht in der `.env`, und die Vorgabe ist `whisper`
(`speech/stt.py:189`). Das trainierte ONNX-Modell ist vorhanden, konsistent
und geprüft — aber der Standardpfad transkribiert stattdessen fortlaufend
überlappende Fenster mit Whisper.

Das ist messbar teuer und fehleranfällig. Aus der Logdatei vom 13.08.,
23:16–23:17 Uhr, im Sekundentakt:

```
speech.stt: gehört: 'swr 2020' (warte auf 'jude angetreten')
speech.stt: gehört: 'copyright wdr 2020' (warte auf 'jude angetreten')
speech.stt: gehört: 'untertitel im auftrag des zdf 2020' (warte auf 'jude angetreten')
```

Das sind Whisper-Halluzinationen auf Stille — die typischen Untertitel-Artefakte
aus den Trainingsdaten. Jedes dieser Fenster kostet einen vollständigen
Whisper-Durchlauf; der Prozess läuft damit dauerhaft unter Last, ohne dass
jemand spricht.

Zusätzlich: `WAKE_WORD_THRESHOLD` steht in der `.env` auf 0,85, während die
Modellmetadaten 0,9917 nennen und der höchste gemessene Negativ-Score bei
0,8099 lag. Wer auf `onnx` umstellt, bekommt mit 0,85 mehr Fehlauslösungen als
nötig.

### 11.7 Der Redakteur fällt still auf das schwächere Modell zurück

Heinz ist auf `cloud_groq_llama` festgelegt. Ist das Groq-Kontingent erschöpft,
überspringt der Router die Stufe (`skipped_kontingent`) und der Lauf landet
auf der nächsten Stufe der Kette. Belegt in `agent_runs`: der Lauf vom 13.08.
um 21:10 lief auf `local_qwen_coder` — also genau auf dem Modell, das laut
`team.py:29–40` sprachlich merklich schwächer schreibt. Der Statuswert lautet
trotzdem `abgeschlossen`; es gibt keinen Hinweis darauf, dass der Text von der
Ersatzstufe kam.

Dasselbe gilt für Judes Chefprüfung, die dieselbe Stufe benutzt.

### 11.8 Alle 16 Agentenläufe stehen auf „abgeschlossen"

Darunter der Lauf `social` vom 13.08., 17:30, auf `cloud_groq_llama` **ohne
einen einzigen Werkzeugaufruf** — genau der Fall, den `_bewerten` heute als
`teilweise` einstufen würde. Der Eintrag stammt aus der Zeit vor der
Korrektur. Ältere Einträge in `agent_runs` sind also nicht belastbar.

### 11.9 Lange Laufzeiten

Aus `agent_runs`: `leadmanager` 679 s auf `local_dolphin`, `leadmanager` 670 s
auf `cloud_claude_sonnet`, `outreach` 511 s. Median der qwen3-Läufe rund 50 s.
Die GUI zeigt deshalb einen „Jude denkt …"-Platzhalter (`app.js:13`), aber es
gibt keine Abbruchmöglichkeit und keinen Fortschrittsbalken.

### 11.10 Kein Weg zu selbst erzeugten Werkzeugen in der GUI

`create_new_tool` erzeugt nur einen Entwurf. Freigegeben wird ausschließlich
über die Konsolenbefehle `/show-tool` und `/approve-tool` (`main.py:190–195`),
die es im GUI-Betrieb nicht gibt. Die Freigabe verlangt zudem ein lauffähiges
Docker (`tool_creator.py:156–161`).

### 11.11 Zwei Aufräumfunktionen ohne Aufrufer

`SubAgentService.forget_notes` und `forget_lehren` (`team.py:159`, `211`)
werden nirgends aufgerufen — weder von einem Werkzeug noch von einem
Endpunkt. Das Gedächtnis und die Lehren eines Mitarbeiters lassen sich nur
durch Löschen der JSON-Dateien unter `DATA_DIR/sub_agent_memory/` bzw.
`sub_agent_lessons/` zurücksetzen.

### 11.12 Person und Alter lassen sich in der GUI nicht setzen

`POST /api/agents` (`web/app.py:346–348`) reicht nur `name`, `role`, `skills`
und `model` weiter. Die Felder `person` und `alter`, die `team.create`
unterstützt, sind über die Oberfläche nicht erreichbar — sie kommen
ausschließlich aus `scripts/team_setup.py`.

### 11.13 Notion-Abfragen blättern nicht

`NotionDatabaseService.query` (`notion_db.py:146–163`) sendet nur eine Seite
mit maximal 100 Einträgen und wertet `has_more` nicht aus. Bei den 66
Kontakten und 78 Content-Stücken reicht das heute; bei über 100 Einträgen
sieht ein Agent den Rest nicht. Die Textsuche filtert zudem erst nachträglich
über diese eine Seite.

### 11.14 Systemd-Unit nicht lauffähig

`AGENT/deploy/jarvis.service` zeigt durchgängig auf
`/home/d4sd1ng/Dokumente/Jarvis/`. Das ist ein Symlink auf den heutigen Ort,
die Unit würde also starten — aber sie heißt noch `jarvis`, startet ohne
`--voice` und wird nach Aussage der Verzeichnisstruktur derzeit nicht benutzt
(Autostart läuft über `Jude-Autostart.desktop`).

### 11.15 Der privilegierte Freibrief ist standardmäßig an

`services/actions.py:16` liest `JUDE_PRIVILEGED` mit der Vorgabe `"true"` — die
Variable steht nicht in der `.env`, der Freibrief ist also aktiv. Ein
bestätigter `shell_command` läuft mit `shell=True` und den vollen Rechten des
Jude-Prozesses (`actions.py:86`). Der einzige Schutz davor ist der
Bestätigungsdialog. Wer das nicht will, setzt `JUDE_PRIVILEGED=false`.

In `confirmations` steht bereits ein ausgeführter und ein fehlgeschlagener
`shell_command`.

### 11.16 Was nicht geprüft werden konnte

- **MT5-Anbindung.** `MT5_MCP_COMMAND` ist gesetzt, aber ob der Server läuft
  und Daten liefert, wurde nicht getestet (`ict.stack_status` hätte den
  Prozess gestartet). Die Tabelle `trading_cards` ist leer — es wurde also
  noch nie eine Karte gespeichert.
- **Home Assistant.** Konfiguration vorhanden, Erreichbarkeit nicht geprüft.
- **Blender.** `BLENDER_BIN` ist nicht gesetzt; ob ein Blender im PATH liegt,
  wurde nicht geprüft.
- **Postfächer.** Ob die fünf IMAP-Zugänge tatsächlich funktionieren, lässt
  sich nur durch eine Anmeldung feststellen; das wurde nicht getan.

---

## 12. Auffälligkeiten

Echte Fehler und Widersprüche, die beim Lesen aufgefallen sind — mit Pfad und
Begründung.

### A1 · `.env.example` führt die falschen Namen für die GUI-Zugangsdaten

`AGENT/.env.example:15–16` definiert `JARVIS_GUI_USER` und
`JARVIS_GUI_PASSWORD`. Der Code liest `JUDE_GUI_USER` und `JUDE_GUI_PASSWORD`
(`web/app.py:91`). Dasselbe bei `JARVIS_HOST` / `JARVIS_PORT` (Z. 45–46) gegen
`JUDE_HOST` / `JUDE_PORT`.

Wer die Vorlage benutzt, hat keine Authentifizierung. Immerhin fällt der
Fehler in die sichere Richtung: `web/app.py:94–95` weist nicht-lokale Zugriffe
ohne Zugangsdaten mit 403 ab, statt sie durchzulassen. Die echte `.env`
verwendet korrekt `JUDE_*`. Zusätzlich fehlen in der Vorlage
`GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY` und sämtliche
`NOTION_*`.

### A2 · Die Revisionsschleife schließt sich nicht

`services/review.py:129` (`erledigt`) hat keinen Aufrufer im Produktivcode.
Siehe [11.4](#114-eine-revision-kommt-nie-aus-dem-revisionszustand-heraus).
Das ist der schwerwiegendste funktionale Befund: die Rückkopplung zwischen
Tinos Anmerkung und der nächsten Runde des Mitarbeiters bleibt offen.

### A3 · Der News-Ticker zeigt „undefined"

`web/static/app.js:173` baut `newsItems` als Liste von **Zeichenketten**
(`` `${topic}: ${t}` ``), Z. 174 liest daraus aber `n.title`. Das Feld gibt es
nicht; im Ticker steht dauerhaft `undefined`.

### A4 · Ein Fehler in Zeile 9 verhindert die Postfachanzeige

`web/static/app.js:9` schreibt in `$('#stats')`. Dieses Element existiert in
`index.html` nicht (es gibt nur `#routingStats`). Der Zugriff auf `null` wirft,
der `.then`-Block bricht ab, und `$('#mailStatus')` — direkt dahinter — wird
nie befüllt. Der Postfachzustand im Werkzeuge-Tab bleibt leer.

### A5 · Widersprüchliche Angaben zum Radar-Zeitraum

`services/dwd_radar.py:1` sagt im Modulkopf „30 min Rückschau, 90 min
Vorhersage". Die Konstante `PAST_RUNS = 18` (Z. 42) ergibt mit 5-Minuten-Takt
**90 min** Rückschau — der Kommentar daneben sagt das auch. Dieselbe falsche
Angabe steht in `web/app.py:430`. Richtig ist: 90 min zurück, 90 min voraus.

### A6 · README und Audit beschreiben einen anderen Stand

`AGENT/README.md` heißt noch „Jarvis", nennt `--wake-word "hey jarvis"`
(Z. 94) statt „Jude angetreten", `whisper-small` als Standard (tatsächlich
base, `stt.py:29–31`), `de_DE-thorsten-medium` als Stimme (tatsächlich high,
`tts.py:57`), und kennt weder Team, Abnahme, Scheduler, Backup, RAG, Health,
SSH noch Notion.

`AGENT/docs/completeness_audit.md` (Stand 23.07.) nennt 31 bestandene Tests —
es sind 77 — und behauptet, das Repository habe keinen Commit.

Beide verweisen auf `ZUGANGSDATEN_EINTRAGEN.md` als „einzige vollständige
Übergabeliste"; **diese Datei existiert nicht**. Derselbe tote Verweis steht
in `AGENT/docs/configuration_checklist.md`.

`AGENT/docs/visualization_contract.md` kennt `services/dwd_radar.py` nicht und
nennt „max Zoom 7", während `radar.py:36` `max_zoom: 12` liefert.

`AGENT/docs/training.md` beschreibt in Kapitel 4 das Wake-Word-Training,
ohne zu erwähnen, dass der trainierte ONNX-Vorfilter zur Laufzeit
standardmäßig gar nicht benutzt wird.

### A7 · Ungeschützte `.env`-Sicherungskopien

Neben `AGENT/.env` liegen drei Kopien: `.env.bak-1785964414`,
`.env.bak-1786484350`, `.env.bak-1786487629`. Sie enthalten dieselben
Schlüssel. Das Muster `*.bak-*` ist kein Standardeintrag in `.gitignore` — ob
sie erfasst sind, sollte geprüft und die Kopien danach gelöscht werden.
Zusätzlich liegt dort eine verwaiste LibreOffice-Sperrdatei `.~lock..env#`,
was bedeutet, dass die `.env` mit LibreOffice geöffnet wurde.

### A8 · Uneinheitliche Flag-Konvention bei den Skripten

Fünf Skripte benutzen `--apply` für den Schreibmodus,
`scripts/training_export.py:108` benutzt `--write`. Wer sich `--apply`
angewöhnt hat, führt den Export im Trockenlauf aus und wundert sich über die
fehlende Datei.

### A9 · Zwei fast identische Trainingsskripte

`scripts/train_hey_jude.py` und `scripts/train_wakeword.py` überschneiden sich
zu etwa 70 %. Nur `train_wakeword.py` hat die Datei-Level-Validierung und die
harte Qualitätsschranke (kein Modell unter 90 % Recall). `train_hey_jude.py`
ist die überholte Fassung und sollte entfernt werden, damit niemand versehentlich
ein ungeprüftes Modell erzeugt.

### A10 · `news.py` spricht mit einem anderen Dienst, als die Beschreibung sagt

Das Werkzeug heißt „Aktuelle Crypto-News über **NewsAPI** abrufen"
(`tools/skills.py:42`), der Dienst ruft aber `https://newsdata.io/api/1/latest`
(`services/news.py:59`). Der Klassenkopf hält fest, dass der alte Code gegen
newsapi.org lief und „dauerhaft 401 erntete". Der Variablenname `NEWS_API_KEY`
verstärkt die Verwechslung — er gehört heute zu newsdata.io.

Dazu zwei tote Parameter: `fetch(language, hours, limit)` (`news.py:33`)
verwendet `hours` und `limit` im ganzen Rumpf nicht; die Menge steht fest bei
10 je Bereich (Z. 39). Das Werkzeugschema bietet beide trotzdem an
(`skills.py:43`) — ein Aufruf mit `limit=5` liefert weiterhin bis zu 16
Artikel.

Auch der Klassenname `CryptoNewsService` passt nicht mehr: die Weltlage hat mit
8 Artikeln das größte Gewicht.

### A11 · `shopping_compare` kann Marke und Größe nicht setzen

`ShoppingService.compare(category, brand, size, limit)` hat vier Parameter, das
Werkzeugschema (`tools/skills.py:58–59`) bietet nur `category` und `limit`.
Damit gilt immer `brand="gstar"` und `size="XXL"`. Über den Agenten ist Nike
also **gar nicht erreichbar**, obwohl fünf Nike-Kategorien im Dienst hinterlegt
sind. Nur der GUI-Endpunkt `POST /api/shopping` (`web/app.py:549–552`) reicht
alle drei Werte durch.

### A12 · Die Kill Zones stehen an zwei Orten und driften auseinander

`services/ict.py:102` liest sie aus `ICT_KILL_ZONES` bzw. benutzt geprüfte
Standardfenster. `services/ict_training.py:88–91` hat sie dagegen **hart
einprogrammiert** (20:00–24:00, 02:00–05:00, 08:30–12:00, New Yorker Zeit).
Wer `ICT_KILL_ZONES` ändert, trainiert das Gate auf andere Fenster, als der
Live-Lauf benutzt — ohne dass irgendetwas darauf hinweist.

In derselben Zeile steht zudem ein toter Teilausdruck: `minute < 0` kann bei
`minute = hour*60 + minute` nie wahr werden.

### A13 · Zwei fruchtlose Abbruchversuche im Fakten-Prüfer

`services/fact_checker.py:96–99`: sobald drei Belege zusammen sind, wird über
alle Futures `cancel()` gerufen und die Schleife verlassen.
`Future.cancel()` stoppt keine bereits gestartete Aufgabe, und der umgebende
`with ThreadPoolExecutor`-Block wartet beim Verlassen ohnehin auf alle Futures.
Der vorzeitige Abbruch spart also keine einzige HTTP-Anfrage ein.

Dazu fehlt dem Werkzeug eine Obergrenze: 8 Behauptungen × bis zu 3 Suchen ×
bis zu 6 Extraktionen sind der Worst Case eines einzigen `fact_check_url`.

### A14 · `connection()` legt bei jedem Zugriff das Schema neu an

`services/database.py:126` ruft in jedem `connection()`-Aufruf
`initialize_database()`. Das bedeutet vor **jedem Lesen** ein `executescript`
mit 15 `CREATE TABLE IF NOT EXISTS`, einem `PRAGMA journal_mode=WAL` und einer
`PRAGMA table_info`-Migrationsprüfung. In heißen Pfaden wie `memory.context()`
(die je Anfrage 200 Erinnerungen und 400 Gesprächsbeiträge lädt) ist das
messbar teuer.

Nebenbei: `PRAGMA foreign_keys=ON` steht im Setup-Skript, wird aber auf der
eigentlichen Arbeitsverbindung nicht gesetzt (PRAGMA ist verbindungslokal).
Folgenlos, weil kein `FOREIGN KEY` deklariert ist — aber irreführend.

### A15 · `coding_test` führt fremden Projektcode ohne Bestätigung aus

`CodingService.test` (`services/coding.py:85–106`) erkennt die Toolchain und
startet `pytest`, `npm test`, `cargo test` oder `go test`. Das Werkzeug ist in
`tools/skills.py:62` **ohne** `confirm_action` registriert. Ein
`package.json`-Testskript ist damit ein direkter Ausführungspfad für beliebigen
Code, sobald ein Repository unter AI-Data liegt — an der Bestätigungsschleife
vorbei, die für `coding_write`, `coding_commit` und `coding_push` gilt.

`repositories()` (Z. 12–26) scannt zudem per `rglob(".git")` die gesamte
AI-Data-Platte ohne Tiefenbegrenzung und führt je Treffer drei Git-Kommandos
aus.

### A16 · SSH ist ohne `JUDE_SSH_HOSTS` fail-open

`services/remote.py:22–33`: Ist die Variable nicht gesetzt, gilt **jeder**
Host-Alias aus `~/.ssh/config` als freigegeben. Die Variable steht zwar in der
`.env`, aber der Ausfallpfad öffnet im Zweifel die gesamte SSH-Infrastruktur
statt zu sperren. Dazu `StrictHostKeyChecking=accept-new` (Z. 18), das
unbekannte Hostkeys beim ersten Verbindungsaufbau automatisch annimmt.

Beim Upload wird `local_path` nur mit `resolve_path` ohne `for_write` geprüft
(Z. 51–60) — es lassen sich also Dateien von **außerhalb** AI-Data hochladen,
während der Download auf AI-Data beschränkt bleibt.

### A17 · `exclude_last_turn` löscht die Kandidaten nicht, die es löschen soll

`services/memory.py:147–157`: Nach „nicht speichern" wird
`DELETE FROM memory_items WHERE normalized=?` mit dem **normalisierten
Volltext** des letzten Beitrags ausgeführt. Die automatisch erfassten
Kandidaten sind aber **Satzfragmente** dieses Beitrags
(`capture_candidates`, Z. 110–119 zerlegt an Satzzeichen). Der Löschversuch
trifft sie deshalb nicht. Nur die Fingerprint-Sperre für den Volltext greift.

### A18 · Zwei getrennte Notion-Dienste, zwei getrennte Wetter-Anbindungen

- `services/notion.py` (Rezepte) und `services/notion_db.py` (generisch) sind
  unabhängige Implementierungen mit eigener Header- und Query-Logik. Nur der
  zweite ist als Werkzeug registriert; die Rezepte kann der Agent also nicht
  erreichen, obwohl `rezepte` in `DATABASES` steht.
- `services/weather.py` und `tools/wetter.py` rufen Open-Meteo unabhängig
  voneinander auf. Der Dienst cached 600 s, das Werkzeug nicht.

Ebenso doppelt: `services/filesystem.py:47–60` (`write_text`) und
`:63–77` (`write_external_after_confirmation`) sind bis auf die
Schreibbegrenzung zeichengleich.

### A19 · Zwei unterschiedliche Plattenmessungen im Cockpit

`services/health.py:36–37` misst `shutil.disk_usage(AI_DATA_ROOT)` und teilt
durch `1e9` (dezimale GB). `services/system_monitor.py` misst `DATA_DIR` und
teilt durch `1024**3` (binäre GiB). Die beiden Anzeigen weichen systematisch
um rund 7 % voneinander ab.

`system_monitor.py` liest außerdem ausschließlich aus `/proc` und
`/sys/class/hwmon` — unter Windows, das `core/paths.py:8` ausdrücklich
vorsieht, wirft jeder dieser Zugriffe. Es gibt keinen Plattform-Guard.

### A20 · Hartkodierte Pfade außerhalb von `core/paths.py`

`core/paths.py:11–12` verlangt: „kein Modul außer diesem darf absolute
Datenpfade enthalten." Verletzt wird das an vier Stellen:

| Fundstelle | Pfad |
|---|---|
| `services/ict.py:30` | `/home/d4sd1ng/trading/venv/bin/python`, `/home/d4sd1ng/trading/mt5_mcp.py` als Vorgabe für `MT5_MCP_COMMAND` |
| `services/backup.py:42` | `AI_DATA_ROOT/Projects/Jude/AGENT/config/models.yaml` — nimmt an, dass das Repository genau dort liegt |
| `services/ocr.py:36` | `AI_DATA_ROOT/Jude/tmp` statt `JUDE_DIR` |
| `services/health.py:61` | Dateiname `jude_angetreten.onnx` ohne Konfigurationsmöglichkeit |

Dazu stehen in `services/mail.py:19–23` fünf personenbezogene E-Mail-Adressen
fest im Code statt in der Konfiguration.

### A21 · Kleinere Widersprüche im Code

| Fundstelle | Befund |
|---|---|
| `speech/tts.py:9` gegen `:202` | Docstring nennt Vorgabe-Pitch 1.06, der Code nutzt 1.0 |
| `speech/tts.py:13` gegen `:101` | Einmal „1 dB unter Vollausschlag", einmal „3 dB Reserve" für denselben Wert 0,89 |
| `speech/controller.py:54` | `user_name` wird gelesen und nie verwendet |
| `services/memory.py:36` | `import os` in `__init__`, ohne Verwendung |
| `speech/stt.py:247` | Typannotation `listener: WakeWordListener`, obwohl `WakeWordListener` (Z. 186) eine Factory-Funktion und keine Klasse ist |
| `services/notion.py:171` | `lock_days` (Rotationssperre) wird gelesen, aber von `today()` nicht ausgewertet |
| `services/market.py:17` | `MARKETS["XAU/EUR"]["symbol"] = "GC=F/EURUSD=X"` ist ein toter Wert — `_fetch_yahoo:66` setzt die Ticker selbst |
| `services/notifications.py:38` | `mark_read` wirft bei einer bereits gelesenen Nachricht `ValueError`; nicht idempotent. Die Tabelle hat zudem keine Rotation und wächst unbegrenzt |
| `services/scheduler.py:161` | Greift auf das private `self.voice._speak(...)` zu und verschluckt jeden Fehler |
| `services/scheduler.py:36–38` | Schreibt `scheduled_tasks.json` nicht atomar, obwohl `filesystem.write_text` genau dafür da ist |
| `services/calendar.py:30–31` | Else-Zweig für `dt.tzinfo is None` ist unerreichbar, weil Z. 18–19 beiden Zeitpunkten vorher eine Zeitzone geben |
| `services/documents.py:88–93` | Embeddet jeden Abschnitt innerhalb einer offenen SQLite-Transaktion — die Verbindung bleibt über alle Netzaufrufe offen |
| `services/briefing.py:5–6` | Docstring verspricht NewsAPI-Vorrang; `_topic_headlines:205` ruft ausschließlich Google News |
| `services/briefing.py:116` | Ruft `market.fetch(...)` statt `history(...)` — jedes Briefing löst einen Netzabruf plus DB-Schreibvorgang je Markt aus |
| `services/actions.py:84` | Schreibt den Audit-Eintrag mit `success=True` **vor** der Ausführung; gescheiterte Befehle stehen im Log als erfolgreich |
| Ungenutzte Importe | `backup.py:11` (`shutil`), `calendar.py:5` (`Path`), `database.py:4` (`os`), `ict.py:12` (`Path`), `ict_training.py:4` (`os`) |
| `web/static/app.js:113` | `resize`-Listener klickt ohne Entprellung `#loadMarket` — jede Fensteränderung löst eine Marktabfrage aus |
| `web/app.py:58` | Legt eine zweite `ConfirmationQueue` an, obwohl `build_application` schon eine erzeugt hat. Funktional folgenlos (beide sind zustandslos und arbeiten auf derselben Tabelle), aber irreführend |
| `services/database.py` | Die Tabellen `reviews` und `rag_chunks` fehlen im zentralen Schema und werden von ihren Diensten selbst angelegt |

### A22 · `local_first` macht die Modellauswahl teilweise wirkungslos

`config/models.yaml:142` setzt `local_first: true`. In
`model_router.py:460–461` reduziert das die Kandidatenliste **immer** auf
lokale Modelle — der gesamte Bewertungsapparat in `score()` (Z. 479–487) mit
Kostenabschlag, Latenz und gelernter Anpassung entscheidet damit nur noch
zwischen dolphin3 und qwen3. Alle Cloud-Tags, -Prioritäten und -Gewichte sind
für die *Auswahl* wirkungslos; sie zählen nur in der Fallback-Kette. Das ist
vermutlich so gewollt („lokal zuerst"), aber der Code liest sich, als würde
hier zwischen elf Modellen abgewogen.
