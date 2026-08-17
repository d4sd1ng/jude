"""Wacht über das kostenfreie Groq-Tageskontingent.

Groqs freie Stufe gibt 100.000 Token pro Tag und 12.000 pro Minute. Beides war
bisher unsichtbar: der Verbrauch fiel erst auf, wenn ein Lauf mit HTTP 429
abbrach – gemessen am 13.08. um 20:55 standen 94.069 von 100.000 auf der Uhr,
ohne dass irgendwo etwas davon zu sehen gewesen wäre. Ein einzelner langer
Agentenlauf kann den Rest eines Tages in einem Zug verbrauchen.

Zwei Dinge passieren hier:

**Messen** – Groq liefert den Restbestand in jeder Antwort mit
(``x-ratelimit-remaining-tokens`` und Verwandte). Das ist die belastbare Zahl,
nicht unsere eigene Zählung: sie berücksichtigt auch Anfragen, die gar nicht
über Jude liefen. Die Werte werden hier festgehalten.

**Bremsen** – unterhalb der Reserve wird Groq gar nicht erst gefragt. Das spart
nicht nur den vergeblichen Aufruf, es hält den Rest für das frei, was ihn
wirklich braucht: Judes Prüfung und der Redakteur. Hintergrundarbeit soll das
Kontingent nicht leerräumen.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from core.paths import DATA_DIR

#: Ab hier wird nicht mehr angefragt. 15 % von 100.000 Token bleiben für den
#: Fall stehen, dass Tino selbst etwas braucht.
RESERVE_ANTEIL = 0.15
#: Was die freie Stufe hergibt, solange Groq nichts anderes meldet.
TAGESLIMIT = 100_000

_DATEI = DATA_DIR / "groq_kontingent.json"

#: "Used 82355" / "Limit 100000" / "try again in 5h8m58.848s" aus dem 429-Rumpf.
_GEBRAUCHT = re.compile(r"Used\s+(\d+)")
_LIMIT = re.compile(r"Limit\s+(\d+)")
_WARTEN = re.compile(r"try again in\s+(?:(\d+)h)?(?:(\d+)m)?([\d.]+)?s")


def _laden() -> dict:
    try:
        return json.loads(_DATEI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _speichern(stand: dict) -> None:
    try:
        _DATEI.parent.mkdir(parents=True, exist_ok=True)
        _DATEI.write_text(json.dumps(stand, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass            # Buchhaltung darf einen Lauf nie scheitern lassen


def kopfzeilen_merken(headers) -> None:
    """Die Restwerte aus einer geglückten Groq-Antwort übernehmen."""
    def zahl(name: str) -> int | None:
        wert = headers.get(name)
        try:
            return int(float(str(wert)))
        except (TypeError, ValueError):
            return None

    rest_tag = zahl("x-ratelimit-remaining-tokens")
    limit_tag = zahl("x-ratelimit-limit-tokens")
    if rest_tag is None:
        return
    stand = _laden()
    stand.update({
        "gemessen_am": datetime.now(timezone.utc).isoformat(),
        "rest": rest_tag,
        "limit": limit_tag or stand.get("limit") or TAGESLIMIT,
        "quelle": "header",
    })
    stand.pop("gesperrt_bis", None)
    _speichern(stand)


def grenze_merken(fehlertext: str) -> None:
    """Aus einem 429 lernen: Verbrauch, Limit und wann es weitergeht.

    Der Rumpf ist genauer als jede Schätzung – er nennt den Tagesverbrauch auf
    das Token genau und die Wartezeit auf die Sekunde.
    """
    gebraucht = _GEBRAUCHT.search(fehlertext)
    limit = _LIMIT.search(fehlertext)
    warten = _WARTEN.search(fehlertext)
    if not gebraucht:
        return
    stand = _laden()
    obergrenze = int(limit.group(1)) if limit else stand.get("limit") or TAGESLIMIT
    stand.update({
        "gemessen_am": datetime.now(timezone.utc).isoformat(),
        "rest": max(0, obergrenze - int(gebraucht.group(1))),
        "limit": obergrenze,
        "quelle": "429",
    })
    if warten:
        sekunden = (int(warten.group(1) or 0) * 3600 + int(warten.group(2) or 0) * 60
                    + float(warten.group(3) or 0))
        stand["gesperrt_bis"] = (datetime.now(timezone.utc)
                                 + timedelta(seconds=sekunden)).isoformat()
    _speichern(stand)


def stand() -> dict:
    """Was noch da ist – für die Anzeige und für die Bremse."""
    roh = _laden()
    limit = int(roh.get("limit") or TAGESLIMIT)
    # Groqs Tageslimit setzt sich täglich zurück – eine Messung von gestern
    # sagt über heute nichts. Ohne diesen Reset bremste der Stand vom 13.08.
    # noch am 15.08. jede Groq-Anfrage aus.
    gemessen = roh.get("gemessen_am")
    neuer_tag = False
    if gemessen:
        try:
            neuer_tag = (datetime.fromisoformat(gemessen).date()
                         < datetime.now(timezone.utc).date())
        except ValueError:
            neuer_tag = True
    rest = limit if neuer_tag else int(roh.get("rest", limit))
    gesperrt = False
    frei_ab = roh.get("gesperrt_bis")
    if frei_ab:
        try:
            gesperrt = datetime.fromisoformat(frei_ab) > datetime.now(timezone.utc)
        except ValueError:
            frei_ab = None
    return {
        "limit": limit,
        "rest": rest,
        "verbraucht": max(0, limit - rest),
        "anteil": round(100 * max(0, limit - rest) / limit) if limit else 0,
        "reserve": int(limit * RESERVE_ANTEIL),
        "gesperrt": gesperrt,
        "frei_ab": frei_ab if gesperrt else None,
        "gemessen_am": roh.get("gemessen_am"),
        "quelle": "neuer Tag (zurückgesetzt)" if neuer_tag else roh.get("quelle"),
    }


def verfuegbar(geschaetzte_token: int = 0) -> bool:
    """Darf Groq noch gefragt werden?

    Nein, wenn eine gemeldete Sperre läuft oder der Rest unter die Reserve
    fiele. Lieber eine Stufe tiefer antworten, als das Kontingent aufbrauchen
    und danach für den ganzen Tag ohne dazustehen.
    """
    jetzt = stand()
    if jetzt["gesperrt"]:
        return False
    return jetzt["rest"] - max(0, int(geschaetzte_token)) >= jetzt["reserve"]
