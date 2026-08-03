# Jarvis – lokaler modularer Assistent

Jarvis verbindet eine lokale Ollama-KI mit einer responsiven Oberfläche, Fachagenten und einer zentralen Bestätigungswarteschlange. Cloud-Modelle und externe Konten bleiben optional und werden nur mit Schlüsseln aus `AGENT/.env` verwendet.

## Funktionsumfang

- Chat, lokales Modell-Routing, Fallbacks und Plugin-System
- Browser-GUI sowie Desktop-WebView aus derselben Anwendung
- OHLCV: BTC/EUR und BTC/USD über Binance; XAU/USD als Yahoo-Gold-Futures-Proxy und XAU/EUR daraus abgeleitet; SQLite-Historie und CSV
- Crypto-News über NewsAPI mit quellengebundener journalistischer Zusammenfassung
- Regenradar für Berliner Straße, 35039 Marburg
- Home Assistant: Licht an/aus in Wohnzimmer, Schlafzimmer und Flur; Alexa-/Growcontroller-Aktionen ausschließlich aus konfigurierter Allowlist
- OCR für deutsche und englische Bilder/PDFs
- fünf IMAP/SMTP-Postfächer: lesen, suchen, Entwurf und Archiv direkt; Senden/Löschen nach Bestätigung
- Coding-/Test-Agent ausschließlich mit Schreibzugriff unter `/media/d4sd1ng/AI-Data`; Branch, Commit, Push und PR; Löschen/Merge nach Bestätigung
- Shopping-Vergleich über offizielle G-Star-/Nike-Produktdaten mit Preisen, Herren XXL und Schuhe 44; keine Bestellung
- günstiger, validierter Low-Carb-Essensplan für 1–2 Personen und 7–10 Tage als GUI-Daten, vollständige Einkaufsliste und umbruchgeprüftes PDF unter `AI-Data/Essensplan`
- ICT/SMC-Demoanalyse: H4-Bias, H1-Eingrenzung und M1-Entry als Einheit, Trading Cards und Kill-Zone-Scheduler
- Fake-Checker für öffentliche URL-Berichte, Posts und YouTube-Videos; bestätigt erst mit zwei zusätzlichen unabhängigen Quellen
- öffentlicher URL-Scraper mit robots.txt-, Größen-, Redirect- und Privatnetz-Schutz

Der genaue Abnahmestand jedes Bereichs steht in `docs/completeness_audit.md`. Die einzige vollständige Übergabeliste für Schlüssel, IDs, Passwörter und ausstehende Festlegungen ist `../ZUGANGSDATEN_EINTRAGEN.md`.
Das Verhalten von Gedächtnis-, Routing-, ICT- und Wake-Word-Training ist in `docs/training.md` getrennt dokumentiert.

## Installation

```bash
cd /home/d4sd1ng/Dokumente/Jarvis
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r AGENT/requirements.txt
cp AGENT/.env.example AGENT/.env
```

Geheimnisse werden ausschließlich in der gitignorierten Datei `AGENT/.env` eingetragen. Die Datei ist bereits mit leeren Feldern angelegt; `AGENT/.env.example` bleibt die kanonische Vorlage. Die Projektwurzel enthält absichtlich keine zweite, widersprüchliche Konfiguration.

## Start

Vom Verzeichnis `AGENT/` aus:

```bash
cd /home/d4sd1ng/Dokumente/Jarvis/AGENT
../.venv/bin/python main.py --gui --host 127.0.0.1 --port 8765
```

Danach: `http://127.0.0.1:8765`. Für Mobilzugriff über LAN/WireGuard darf auf `0.0.0.0` gebunden werden, aber nur nachdem `JARVIS_GUI_USER` und `JARVIS_GUI_PASSWORD` gesetzt wurden; andernfalls weist Jarvis nicht-lokale Zugriffe ab.

Nach eingetragenen GUI-Zugangsdaten kann der geprüfte Benutzer-Systemd-Entwurf installiert werden:

```bash
mkdir -p ~/.config/systemd/user
cp /home/d4sd1ng/Dokumente/Jarvis/AGENT/deploy/jarvis.service ~/.config/systemd/user/jarvis.service
systemctl --user daemon-reload
systemctl --user enable --now jarvis.service
```

Der Dienst wird nicht automatisch installiert, solange die Remote-Zugangsdaten leer sind. So wird kein ungeschützter Netzwerkdienst veröffentlicht.

Desktopfenster:

```bash
../.venv/bin/python main.py --desktop
```

CLI und Einzelaufruf:

```bash
../.venv/bin/python main.py
../.venv/bin/python main.py --once "Antworte nur mit OK"
```

## ICT/SMC-Zeitsteuerung

Die Definition bleibt in New-York-Ortszeit und wird dynamisch nach Marburg umgerechnet:

| Kill Zone | America/New_York |
|---|---:|
| Asian Range | 20:00–00:00 |
| London Open | 02:00–05:00 |
| New York Open | 08:30–11:00 |
| London Close | 10:00–12:00 |

`America/New_York` und `Europe/Berlin` werden als IANA-Zeitzonen verwendet. Dadurch sind es meist sechs Stunden Differenz, während der unterschiedlichen DST-Umstellungswochen zeitweise fünf. Der Scheduler prüft innerhalb aktiver Zonen pro Minute XAUUSD und BTCUSD. Er erzeugt Meldungen/Trading Cards, aber keine automatische Order.

Der Custom-MCP wird von Jarvis bedarfsgerecht über stdio gestartet. Dauerhaft benötigt werden `mt5-terminal` und der Systemdienst `mt5-bridge`, der `wine python -m mt5linux` auf `127.0.0.1:18812` bereitstellt. Das Demo-Konto muss im Terminal autorisiert sein.

## Bestätigungen und Dateirechte

Direktes Schreiben ist nur unter `/media/d4sd1ng/AI-Data` erlaubt. Außerhalb davon ist Jarvis standardmäßig read-only; eine exakt beschriebene Schreibaktion kann einzeln bestätigt werden. Mail-Senden, Mail-Löschen, Kalendererstellung, Datei-Löschen und Git-Merge landen immer in der GUI unter „Bestätigungen“. Die beim Anlegen gespeicherte Payload wird erst nach Freigabe ausgeführt.

## Sprache

```bash
../.venv/bin/python main.py --voice --wake-word "hey jarvis" --record-seconds 12
```

Jarvis wartet kontinuierlich lokal auf „Hey Jarvis“, bestätigt mit einem Ton und beendet den Befehl nach einer Sprechpause. `WHISPER_MODEL` zeigt standardmäßig auf das installierte Whisper-small-Modell; Piper verwendet `de_DE-thorsten-medium`. `--record-seconds` begrenzt die maximale Befehlsdauer nach dem Aktivierungswort.

## Tests

```bash
cd /home/d4sd1ng/Dokumente/Jarvis/AGENT
../.venv/bin/python -m pytest -q
```

Die Unit-Tests prüfen Kernrouting, Toolschleife, Schreibgrenzen, Scraper-SSRF-Schutz, Markt-Mapping und die New-York-/Marburg-DST-Umrechnung. Live-Dienste benötigen ihre jeweiligen Schlüssel bzw. das autorisierte MT5-Demo-Konto.

Zusätzliche Qualitätsprüfungen:

```bash
../.venv/bin/ruff check .
../.venv/bin/python -m pip check
../.venv/bin/python -m bandit -q -r . -x tests
node --check web/static/app.js
systemd-analyze --user verify deploy/jarvis.service
```

## Struktur

```text
AGENT/
├── config/       Modell- und Routingkonfiguration
├── core/         Agent, Router, Registry und Tool-Erzeugung
├── services/     Markt, Mail, OCR, Radar, ICT, Fake-Checker usw.
├── speech/       optionale Sprachein- und -ausgabe
├── tools/        automatisch geladene Agent-Tools
├── web/          API, responsive GUI und Desktop-Launcher
├── tests/        automatisierte Tests
└── main.py       Startpunkt
```

Die fünf Pflichtdokumente im Projektwurzelverzeichnis enthalten getrennt Übersicht, Vertrag, Entscheidungen, Architekturregeln und offene Aufgaben.