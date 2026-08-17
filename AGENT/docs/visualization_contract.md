# Visualisierungsvertrag

## OHLCV-Zeitreihe

- Aufgabe: aktuellen Verlauf erkennen und historische Kerzen nach Markt/Intervall prüfen.
- Daten: `open_time`, Open, High, Low, Close, Volume, Quelle und Aktualisierungszeit.
- Darstellung: ein fokaler Candlestick-Chart mit direkt sichtbarem letzten Wert und einer zugänglichen Tabellenalternative.
- Interaktion: Markt und Intervall sind Formularzustand; die letzten 50 Kerzen stehen zusätzlich als Tabelle und die gespeicherte Historie als CSV bereit.
- Mobile: Chart zuerst, Filter kompakt darüber, Werte per Tap/Fokus statt nur Hover.
- Zustand: Live, stale, offline und leer bleiben unterscheidbar; letzte gute Daten bleiben sichtbar.
- QA: Kerzenreihenfolge, OHLC-Invarianten, Desktop-/Mobilbreite und Tabellenparität.

## Regenradar Marburg

- Aufgabe: Niederschlag räumlich um 35039 Marburg, Berliner Straße einordnen.
- Daten: RainViewer-Radarframes, EPSG:3857-Kacheln, maximal Zoom 7, zehnminütige historische Frames.
- Darstellung: ruhige OpenStreetMap-Basiskacheln plus blaue Radar-Rasterebene und fester Standortmarker.
- Interaktion: Frame-Schieber, Aktualisieren und Zoomstufen; keine automatische Geolokalisierung.
- Mobile: Karte bleibt zuerst sichtbar; größere Schaltflächen und Landschaftsmodus für breitere Umgebung.
- Zustand: Quellenhinweis, Frame-Zeit, letzte Aktualisierung, stale/offline und statische letzte Kacheln.
- QA: Zentrum Marburg, Kachelkoordinaten, Quellenhinweis, leere API-Antwort und Offlinezustand.

## Essensplan-PDF

- Aufgabe: 7-10 Tage Low-Carb-Mahlzeiten und gruppierte Einkaufsliste druckbar liefern.
- Daten: Tage, Portionen, Mahlzeiten, Zutaten und Mengen.
- Darstellung: textorientierter Report mit klarer Tageshierarchie und Seitenzahlen; keine dekorativen Diagramme.
- Fallback: GUI-Text und JSON bleiben verfügbar, falls PDF-Erzeugung scheitert.
- Barrierefreiheit: strukturierte Überschriften, ausreichender Kontrast und extrahierbarer Text.
- QA: Textprüfung mit pypdf, Rendering mit Poppler und visuelle Kontrolle jeder Seite.