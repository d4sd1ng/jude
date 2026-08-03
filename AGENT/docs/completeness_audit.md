# Vollständigkeits- und Abnahmeaudit

Stand: 23. Juli 2026. `fertig` bedeutet hier implementiert und auf diesem Rechner nachweisbar geprüft. Externe Integrationen bleiben bis zur Prüfung mit echten Zugangsdaten ausdrücklich `wartet auf Zugangsdaten`.

| Bereich | Implementierung | Prüfung | Status |
|---|---|---|---|
| Kernagent, Toolschleife, lokales Routing | Ollama, Fallbacks, Monatsbudget, persistente Nutzung | realer Qwen-End-to-End-Aufruf und automatisierte Tests | fertig |
| Plugin-System | automatisches Laden, zweistufige Freigabe, AST-Regeln, read-only Docker-Sandbox | Container tatsächlich ausgeführt | fertig |
| Sprache | `Hey Jarvis`, lokaler VAD, Whisper-small, Piper Thorsten | Audiogerät geöffnet; Piper-Audio exakt als „Hallo, dies ist Jarvis in Marburg.“ transkribiert | fertig; persönlicher Mikrofontest empfohlen |
| GUI/Desktop | responsive FastAPI-GUI, Desktop-WebView, Basic Auth, Downloadrouten | Uvicorn-, API-, JS- und Auth-Smoke-Test | fertig lokal |
| Fernzugriff | Bind-Konfiguration und Auth-Sperre | WireGuard-Adresse `10.8.0.5` vorhanden | wartet auf GUI-Benutzer/Passwort und anschließenden Remote-Test |
| OHLCV | BTC/EUR, BTC/USD, XAU/USD, XAU/EUR; SQLite und CSV | alle vier Quellen live; Altbestand migriert | fertig für die vier eindeutigen Märkte |
| CCPX | nicht eindeutig definiert | Webprüfung ergab sowohl CCXT-Kontext als auch ETF-Ticker | wartet auf Nutzerdefinition |
| Crypto-News | NewsAPI plus journalistisches quellengebundenes Briefing | Fehlerweg ohne Key geprüft | wartet auf `NEWS_API_KEY` für Live-Abnahme |
| Regenradar | Berliner Straße, 35039 Marburg, RainViewer-Kacheln | Frame-API und echte PNG-Kachel live | fertig |
| Wetter | Open-Meteo mit Orts-Fallback | „Marburg Germany“ live zu Marburg/Deutschland aufgelöst | fertig |
| Home Assistant Licht | Wohnzimmer, Schlafzimmer, Flur an/aus | HTTP-Aufrufe automatisiert geprüft | wartet auf URL, Token und echte Entity-Abnahme |
| Alexa | ausführbarer Home-Assistant-Connector mit JSON-Allowlist, Agent-Tool, API und GUI | nicht konfigurierte Aktion wird abgewiesen; konfigurierte Aktion per HTTP-Mock geprüft | implementiert; wartet auf echte IDs/Aktionen und Live-Abnahme |
| Growcontroller | derselbe Allowlist-Connector, nur statisch vorab freigegebene Profile | Parser, Sperre unbekannter Aktionen und HTTP-Pfad automatisiert geprüft | implementiert; wartet auf echte IDs/Aktionen/Grenzwerte und Live-Abnahme |
| OCR | Bilder und PDFs, Deutsch und Englisch | echter Tesseract-Test mit deutschem/englischem Text; PDF-Pipeline vorhanden | fertig |
| Mail | fünf Konten, lesen, suchen, Entwurf, Archiv; bestätigtes Senden/Löschen | Protokollpfade und Bestätigungslogik getestet | wartet auf fünf App-/Bridge-Zugangssätze |
| Kalender | bestätigte lokale Termine, persistente Liste, ICS-Download | echte ICS-Datei und UTC-Konvertierung geprüft | fertig |
| Coding-Agent | AI-Data-Inventar, Lesen/Schreiben, Diff, Branch, Commit, Push, PR | echtes temporäres Git-Repository; GitHub-Anmeldung geprüft | fertig |
| Test-Agent | Python, Node, Rust und Go projektspezifisch | Jarvis-Testlauf und Kommandoauswahl geprüft | fertig |
| Shopping | Nike und G-Star getrennt, Herren XXL/Schuhe 44, echte Preise, keine Bestellung | alle drei Kategorien live mit je vier offiziellen, größenbezogenen Treffern | fertig |
| Essensplan | 7–10 Tage, 1–2 Personen, Low Carb, günstig, GUI, Einkaufsliste, PDF; kuratierter Fallback mit 30 unterschiedlichen Gerichten | endgültiges 7- und 10-Tage-PDF jeweils auf allen drei Seiten textuell und visuell ohne Überlauf geprüft | fertig |
| ICT/SMC | H4/H1/M1 als Einheit, Kill Zones, Scheduler, Trading Cards, Meldungen | DST-Tests und Systemdienste; MCP read-only | wartet auf gültige MT5-Demoautorisierung |
| Fake-Checker | URL/YouTube, Behauptungen, unabhängige Domains, 2-Quellen-Schwelle | Acht-Behauptungs-Lauf: 300,5 s und echte Zwei-Quellen-Bestätigung; gezielter Beleg-ID-Lauf: 30,3 s und korrekt nur teilweise bestätigt | fertig; Standard sind fünf Kernbehauptungen |
| Scraper | öffentliche HTTP(S)-Seiten, YouTube-Transkript, robots.txt, SSRF-/Größen-/Redirectschutz | öffentlicher Live-Abruf und SSRF-Test | fertig |
| Bestätigungen | Mail senden/löschen, Termin, Merge, Datei löschen, externe Writes | atomare Queue und verschachtelter DB-Zugriff getestet | fertig |
| Sicherheit | keine Secrets im Projektcode, Pfadgrenzen, Remote-Sperre | Ruff, pip check, Bandit ohne mittlere/hohe Funde | fertig; niedrige Subprozess-Hinweise dokumentiert |

## Automatische Prüfbefehle

```bash
cd /home/d4sd1ng/Dokumente/Jarvis
.venv/bin/python -m pytest -q AGENT/tests
.venv/bin/ruff check AGENT
.venv/bin/python -m pip check
.venv/bin/python -m bandit -q -r AGENT -x AGENT/tests
node --check AGENT/web/static/app.js
systemd-analyze --user verify AGENT/deploy/jarvis.service
```

Der Dienstentwurf liegt unter `AGENT/deploy/jarvis.service`. Er wird erst nach GUI-Zugangsdaten und der Entscheidung über lokale bzw. WireGuard-Bindung in die Benutzer-Systemd-Konfiguration installiert; dadurch wird kein ungeschützter Netzwerkdienst veröffentlicht. Ein vollständiger lokaler Fake-Check mit fünf Hauptbehauptungen kann abhängig von Erreichbarkeit und Modellkorrekturen mehrere Minuten dauern; die GUI zeigt währenddessen einen Arbeitszustand.

Der aktuelle automatisierte Gesamtlauf umfasst 31 bestandene Tests. Die abgenommenen Referenz-PDFs liegen unter `/media/d4sd1ng/AI-Data/Essensplan/essensplan_final_audit_v2.pdf` und `/media/d4sd1ng/AI-Data/Essensplan/essensplan_final_audit_10d.pdf`. Das Jarvis-Arbeitsverzeichnis selbst ist ein noch unpubliziertes Git-Repository ohne Commit und ohne Remote; für eine Veröffentlichung fehlen ausschließlich Repository-Name und Sichtbarkeit.