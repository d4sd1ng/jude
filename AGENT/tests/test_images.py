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
