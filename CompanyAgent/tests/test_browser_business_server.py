"""Verify fixture failure/cleanup boundaries before browser tests depend on them.

The integration server is a test host, not a replacement production application.
These checks focus on lost replies, real replay and temporary-data cleanup rather
than duplicating the browser's happy paths or production policy unit tests.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from business.service import PERSONAS
from tests.browser_business_server import create_app


def test_lost_http_reply_keeps_one_committed_refund_and_replay_does_not_extract():
    # The first HTTP response fails only after real workflow execution commits.
    # Reusing the original request must retrieve that result without another write.
    with TestClient(create_app()) as client:
        session = client.post("/customer/sessions", json={"persona_id": "p_duplicate"}).json()
        headers = {"Authorization": "Bearer " + session["token"], "X-Browser-Test-Lose-Reply": "once"}
        body = {"message": PERSONAS[0]["starter"], "conv_id": session["conv_id"], "request_id": "lost-reply"}
        assert client.post("/customer/chat", headers=headers, json=body).status_code == 503
        before = client.get("/__test__/snapshot", headers=headers).json()
        assert (before["refunds"], before["requests"], before["extraction_calls"]) == (1, 1, 1)
        replay = client.post("/customer/chat", headers=headers, json=body)
        assert replay.status_code == 200 and replay.json()["actions"][0]["status"] == "executed"
        after = client.get("/__test__/snapshot", headers=headers).json()
        assert after == before


def test_fixture_removes_only_its_own_temporary_storage_at_shutdown():
    # A normal server shutdown must close SQLite handles before its owned temporary
    # folder disappears. Read the internal path only in the test, never over HTTP.
    app = create_app()
    with TestClient(app):
        folder = Path(app.state.fixture_folder)
        assert (folder / "business.db").is_file()
    assert not folder.exists()
    assert app.state.workflow is None


def test_inspection_requires_a_real_fixture_session_and_unknown_language_cannot_write():
    # Inspection is not a global database backdoor, and this fixed phrase table
    # cannot accidentally treat a novel sentence as natural-language authorization.
    with TestClient(create_app()) as client:
        assert client.get("/__test__/snapshot").status_code == 401
        assert client.get("/__test__/snapshot", headers={"Authorization": "Bearer bad"}).status_code == 401
        session = client.post("/customer/sessions", json={"persona_id": "p_duplicate"}).json()
        headers = {"Authorization": "Bearer " + session["token"]}
        reply = client.post("/customer/chat", headers=headers, json={
            "message": "A sentence outside the fixed fixture vocabulary", "conv_id": session["conv_id"], "request_id": "unknown",
        })
        assert reply.status_code == 200 and reply.json()["intent"] == "clarify"
        snapshot = client.get("/__test__/snapshot", headers=headers).json()
        assert (snapshot["refunds"], snapshot["actions"], snapshot["tickets"]) == (0, 0, 0)
