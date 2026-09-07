from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from services.notion_db import NotionDatabaseService


def _svc():
    svc = NotionDatabaseService()
    svc.key = "test-key"
    svc._schema_cache["social_posts"] = {
        "name": "social_posts", "titel": "social_posts",
        "felder": {"Titel": "title", "Status": "select"},
        "erlaubte_werte": {"Status": ["Idee", "Geplant"]},
    }
    return svc


def _response(json_data, status_ok=True):
    resp = Mock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


@patch("services.notion_db.NotionDatabaseService._database_id", return_value="db-id")
@patch("services.notion_db.requests")
def test_create_rejects_duplicate_title_instead_of_creating_a_second_page(mock_requests, _db_id):
    """Vorher legte notion_create bei jeder Ueberarbeitung (Mitarbeiter ruft im
    Zweifel notion_create statt notion_update auf) einen weiteren Eintrag mit
    demselben Titel an - gemessen 07.09.2026: ein einziger Wochenbeitrag hatte
    am Ende drei Eintraege in social_posts, der eigentlich bearbeitete blieb
    ohne Bild-URL, weil jede Bestaetigung auf eine andere, verwaiste Seite
    zeigte."""
    svc = _svc()
    mock_requests.post.return_value = _response({
        "results": [{"id": "existing-page", "url": "https://notion.so/existing",
                     "properties": {"Titel": {"type": "title",
                                    "title": [{"plain_text": "Wochen-Beitrag KW 36"}]}}}]
    })
    with pytest.raises(ValueError, match="existing-page"):
        svc.create("social_posts", {"Titel": "Wochen-Beitrag KW 36", "Status": "Idee"})
    # Es wurde nur die Suchabfrage abgesetzt, keine Seite angelegt.
    assert mock_requests.post.call_count == 1
    assert "/databases/" in mock_requests.post.call_args[0][0]


@patch("services.notion_db.NotionDatabaseService._database_id", return_value="db-id")
@patch("services.notion_db.requests")
def test_create_succeeds_when_no_title_collision(mock_requests, _db_id):
    svc = _svc()
    query_response = _response({"results": []})
    create_response = _response({"id": "new-page", "url": "https://notion.so/new"})
    mock_requests.post.side_effect = [query_response, create_response]
    result = svc.create("social_posts", {"Titel": "Ein ganz neuer Beitrag", "Status": "Idee"})
    assert result["angelegt"] is True
    assert result["id"] == "new-page"
    assert mock_requests.post.call_count == 2


@patch("services.notion_db.requests")
def test_archive_sends_archived_flag(mock_requests):
    svc = _svc()
    mock_requests.patch.return_value = _response({})
    result = svc.archive("social_posts", "page-to-archive")
    assert result == {"archiviert": True, "id": "page-to-archive", "datenbank": "social_posts"}
    mock_requests.patch.assert_called_once()
    assert mock_requests.patch.call_args[1]["json"] == {"archived": True}
