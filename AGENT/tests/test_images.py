from __future__ import annotations

import base64
from unittest.mock import Mock, patch

from services.images import ImageService


def _response(json_data):
    resp = Mock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def _b64_payload():
    return {"data": [{"b64_json": base64.b64encode(b"fake-png-bytes").decode()}]}


@patch("services.images.requests")
def test_generate_sends_explicit_quality_and_transparent_background(mock_requests, tmp_path, monkeypatch):
    """Beide Felder fehlten frueher komplett - die API fiel auf ihre eigenen
    Vorgaben zurueck. Gemessen: jedes bisherige Bild kam "billig" (ohne
    quality=high) und mindestens einmal komplett deckend statt transparent
    zurueck, weil beides nie ausdruecklich gesetzt war, sondern nur im
    Prompt-Text erhofft."""
    from core.paths import IMAGES_DIR as _unused  # noqa: F401
    monkeypatch.setattr("services.images.IMAGES_DIR", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    svc = ImageService()
    mock_requests.post.return_value = _response(_b64_payload())
    svc.generate("ein Motiv", marke=False)
    sent = mock_requests.post.call_args[1]["json"]
    assert sent["quality"] == "high"
    assert sent["background"] == "transparent"


@patch("services.images.requests")
def test_edit_sends_explicit_quality_and_transparent_background(mock_requests, tmp_path, monkeypatch):
    monkeypatch.setattr("services.images.IMAGES_DIR", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    svc = ImageService()
    mock_requests.post.return_value = _response(_b64_payload())
    svc.edit(b"source-bytes", "aendere etwas")
    sent = mock_requests.post.call_args[1]["data"]
    assert sent["quality"] == "high"
    assert sent["background"] == "transparent"


@patch("services.images.requests")
def test_generate_erfasst_echte_bildkosten_in_model_usage(mock_requests, tmp_path, monkeypatch):
    """generate()/edit() riefen die OpenAI-API bisher direkt auf, an
    ModelRouter.call_llm() (dem einzigen Ort, der Kosten nach model_usage
    schreibt) komplett vorbei - jedes Bild war fuer Judes eigene
    Kostenrechnung unsichtbar. Gefunden 12./13.09.2026: Tinos echte OpenAI-
    Rechnung zeigte $43.21, Judes eigene Aufzeichnung nur $26.82 - genau
    diese fehlenden ~$16 waren Bildkosten."""
    from services.routing_data import ModelUsageRepository
    monkeypatch.setattr("services.images.IMAGES_DIR", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    svc = ImageService()
    payload = _b64_payload()
    payload["usage"] = {"input_tokens_details": {"text_tokens": 50, "image_tokens": 0},
                        "output_tokens": 1560}
    mock_requests.post.return_value = _response(payload)

    svc.generate("ein Motiv", marke=False)

    from datetime import datetime, timezone
    monat = datetime.now(timezone.utc).strftime("%Y-%m")
    total = ModelUsageRepository().monthly_total(monat)
    erwartet = (50 * 5.0 + 1560 * 40.0) / 1_000_000
    assert total == erwartet
    assert total > 0


@patch("services.images.requests")
def test_generate_ohne_usage_feld_schreibt_keine_kosten(mock_requests, tmp_path, monkeypatch):
    """Fehlt 'usage' in der Antwort (z.B. aeltere API-Version), darf nichts
    geschrieben werden - eine erfundene Zahl waere schlimmer als gar keine."""
    from services.routing_data import ModelUsageRepository
    monkeypatch.setattr("services.images.IMAGES_DIR", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    svc = ImageService()
    mock_requests.post.return_value = _response(_b64_payload())

    svc.generate("ein Motiv", marke=False)

    from datetime import datetime, timezone
    monat = datetime.now(timezone.utc).strftime("%Y-%m")
    assert ModelUsageRepository().monthly_total(monat) == 0
