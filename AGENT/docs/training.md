# Training und Lernen in Jude

Stand: 2. August 2026

Jude verändert nicht unkontrolliert die Gewichte eines Basismodells. Lernen ist in vier getrennte, prüfbare Bereiche aufgeteilt.

## 1. Gedächtnis

Stabile, nicht sensible Aussagen wie Vorlieben oder wiederkehrende Arbeitsweisen werden zunächst als Kandidat lokal in SQLite gespeichert. Dieselbe Aussage wird nach der zweiten Erkennung aktiv. In der GUI unter **Gedächtnis** kann sie sofort bestätigt oder vergessen werden.

- `Merk dir …` speichert ausdrücklich.
- `Merk dir das nicht` schließt den vorherigen Turn aus.
- `Vergiss …` löscht passende Einträge und sperrt deren automatische Wiederaufnahme.
- Passwörter, API-Schlüssel, Tokens, PINs und vergleichbare Geheimnisse werden nicht ins Gedächtnis übernommen.

## 2. Modell-Routing

Jede Routingentscheidung speichert Aufgabentyp, Komplexität, gewähltes und tatsächlich verwendetes Modell, Eskalation, Laufzeit und Fehler. **Passt** oder **Unzureichend** im Chat speichert Feedback. Erst drei Bewertungen derselben Kombination aus Aufgabentyp und Modell beeinflussen die Auswahl; einzelne Klicks übersteuern das Routing nicht.

Das ist Online-Feedback für die Auswahl, kein Fine-Tuning des Sprachmodells. Standard bleibt lokal. Cloud wird nur bei unzureichender lokaler Qualität, vorhandenem Schlüssel und freiem Kostenbudget versucht.

## 3. ICT/SMC

`scripts/train_ict.py` lädt historische M1-Daten aus dem read-only MT5-MCP. Daraus entstehen H1- und H4-Kerzen sowie gemeinsame H4-/H1-/M1-, Liquidity-, FVG-, ATR-, Displacement- und Kill-Zone-Merkmale. Labels vergleichen Stop und ein 2R-Ziel in der Zukunft; mehrdeutige Kerzen werden verworfen.

Die letzten 20 Prozent bleiben chronologisch als Walk-forward-Test unangetastet. Produktiv wird ein Modell nur mit mindestens 200 gelabelten Fällen, beiden Klassen, ROC-AUC mindestens 0,55, Precision mindestens 0,60 und mindestens zehn Testsignalen. Ohne bestandenes Modell wird `SETUP_FOUND` technisch zu `TRADE_BLOCKED`. Das Training kann keine Orders senden.

```bash
cd /media/d4sd1ng/AI-Data/Projects/Jude/AGENT
../.venv/bin/python scripts/train_ict.py XAUUSD
../.venv/bin/python scripts/train_ict.py BTCUSD
```

Ein echtes Training benötigt gültige MT5-Demozugangsdaten und genügend historische M1-Kerzen.

## 4. Wake-Word

`scripts/train_wakeword.py` trainiert den lokalen Vorfilter für **„Jude angetreten“**. Positive Varianten und harte ähnliche Negativphrasen werden auf Dateiebene getrennt. Der interne openWakeWord-Nullpuffer wird weder trainiert noch in der Laufzeit bewertet. Ein Kandidat wird nur exportiert, wenn die vollständige Datei-Prüfung mindestens 90 Prozent Recall und höchstens 1 Prozent Fehlaktivierungen erreicht.

In der Laufzeit bestätigt lokales Whisper zusätzlich die vollständige Phrase. Die endgültige Mikrofonabnahme muss mit der Stimme des Nutzers in typischer Entfernung und mit Hintergrundgeräuschen erfolgen. Fehlaktivierungen werden dabei als neue harte Negativbeispiele gesammelt; sie werden nicht automatisch ungeprüft ins produktive Modell übernommen.

## Grenzen

Gesprächsverläufe, Erinnerungen und Feedback sind lokale Trainingsdaten. Für ein späteres echtes LLM-Fine-Tuning werden sie erst nach manueller Sichtung und ausdrücklicher Freigabe exportiert. Ohne diese Freigabe findet kein Upload und kein Basismodell-Fine-Tuning statt.