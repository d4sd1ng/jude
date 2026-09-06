from __future__ import annotations

from contextlib import suppress

from fastapi import APIRouter, HTTPException

from web.app import reviews, team

router = APIRouter()

# --- Abnahme ----------------------------------------------------------------
# Nicht zu verwechseln mit den Bestaetigungen darueber: die halten eine Aktion
# an, bis Tino zustimmt. Hier legt ein Mitarbeiter etwas Fertiges vor und
# arbeitet sofort weiter; Tino nimmt ab oder schickt es mit einer Anmerkung
# zurueck.


@router.get("/api/reviews")
def review_list(status: str = "offen", limit: int = 50, art: str | None = None):
    return {"vorlagen": reviews.liste(status, limit, art),
            "zusammenfassung": reviews.zusammenfassung(),
            "offen_nach_art": reviews.offen_nach_art()}


@router.get("/api/reviews/{review_id}")
def review_show(review_id: str):
    try:
        return reviews.zeigen(review_id)
    except KeyError:
        raise HTTPException(404, "Unbekannte Vorlage")


@router.post("/api/reviews/{review_id}/{entscheidung}")
def review_decide(review_id: str, entscheidung: str, payload: dict | None = None):
    if entscheidung not in {"abnehmen", "revision"}:
        raise HTTPException(400, "Entscheidung muss abnehmen oder revision sein")
    anmerkung = str((payload or {}).get("anmerkung", "")).strip()
    if entscheidung == "revision" and not anmerkung:
        raise HTTPException(400, "Für eine Revision wird eine Anmerkung benötigt")
    try:
        if entscheidung == "abnehmen":
            ergebnis = reviews.abnehmen(review_id, anmerkung)
            # Bisher hielt das Team nur fest, was schiefging – nie, was gut war.
            # Tino soll dafuer nichts extra tippen muessen: eine Abnahme ohne
            # jede Revision ist selbst schon das Signal – automatisch erkannt
            # aus dem Revisionsverlauf, nicht aus einem getippten Lob.
            if int(ergebnis.get("runde") or 1) <= 1:
                with suppress(Exception):
                    team.lob_merken(
                        ergebnis["agent"],
                        f"„{ergebnis.get('titel', '')}“ ({ergebnis.get('art', '')}) "
                        "im ersten Anlauf ohne Revision abgenommen.",
                        quelle="automatisch")
            if anmerkung:
                with suppress(Exception):
                    team.lob_merken(ergebnis["agent"], anmerkung, quelle="Tino")
            return ergebnis
        ergebnis = reviews.revision(review_id, anmerkung)
    except KeyError:
        raise HTTPException(404, "Unbekannte Vorlage")
    # Tinos Anmerkung wiegt schwerer als jede andere: sie wird zur Lehre, damit
    # derselbe Einwand nicht bei jeder Vorlage aufs Neue kommt.
    with suppress(Exception):
        team.lehre_merken(ergebnis["agent"], anmerkung, quelle="Tino")
    return ergebnis
