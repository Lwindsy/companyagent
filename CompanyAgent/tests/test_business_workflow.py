import asyncio
import json

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from api.business_routes import create_business_router
from business.db import Database
from business.models import BusinessError
from business.service import BusinessService
from workflows.models import ExtractedPlan
from workflows.service import WorkflowService
from workflows.sessions import SessionService


class FixedExtractor:
    # A local fake chooses the plan without language-model I/O. calls measures whether
    # replay/recovery accidentally extracts again; tests may change plan between turns.
    def __init__(self):
        self.calls = 0
        self.plan = ExtractedPlan(intent="refund", reason_code="other")

    async def extract(self, message, recent, overview):
        self.calls += 1
        return self.plan


@pytest.fixture
def workflow(tmp_path):
    # Each test gets real SQLite in a private temporary directory and a deterministic
    # UTC-second clock. No Redis, Chroma, application lifespan, or external model starts.
    db = Database(str(tmp_path / "workflow.db"))
    db.initialize()
    service = BusinessService(db, clock=lambda: 1_800_000_000)
    return WorkflowService(service, FixedExtractor())


@pytest.fixture
def api(workflow):
    # Mount only the independent router. This small test-only staff checker accepts a
    # literal fixture token and must never be mistaken for production authentication.
    def admin(authorization: str = Header(default="")):
        if authorization != "Bearer admin-test":
            raise HTTPException(401)
        return "admin-test"

    app = FastAPI()
    app.include_router(create_business_router(lambda: workflow, admin, lambda: True))
    return TestClient(app)


def start(api, persona="p_b2b"):
    # Create a server-issued customer session and return both response data and bearer
    # headers, mirroring a browser without trusting a body-supplied customer identity.
    response = api.post("/customer/sessions", json={"persona_id": persona})
    assert response.status_code == 200
    session = response.json()
    return session, {"Authorization": "Bearer " + session["token"]}


def chat(api, session, headers, message="Refund", request_id="message-1", **extras):
    # Default request_id repeats the same message; explicit new IDs represent new turns.
    # **extras lets tests try forbidden fields at the actual HTTP validation boundary.
    return api.post("/customer/chat", headers=headers, json={"message": message, "conv_id": session["conv_id"],
                                                           "request_id": request_id, **extras})


def test_api_refund_approval_status_and_exact_message_replay(api, workflow):
    # Follow pending -> staff approval -> fresh status query. Exact reply replay must
    # avoid another extraction, and pending wording must not falsely promise a refund.
    session, headers = start(api)
    response = chat(api, session, headers)
    assert response.status_code == 200
    body = response.json()
    assert body["actions"][0]["status"] == "pending"
    assert "not been refunded" in body["response"]
    assert chat(api, session, headers).json() == body
    assert workflow.extractor.calls == 1
    action_id = body["actions"][0]["id"]
    result = api.post(f"/admin/actions/{action_id}/decide", headers={"Authorization": "Bearer admin-test"},
                      json={"decision": "approve", "note": "Verified"})
    assert result.json()["action"]["status"] == "executed"
    workflow.extractor.plan = ExtractedPlan(intent="status")
    after = chat(api, session, headers, "Any update?", "message-2").json()
    assert after["actions"][0]["status"] == "executed"
    assert "executed" in after["response"]


def test_duplicate_and_expired_stories_use_fixed_code_not_model_claims(api, workflow):
    # The same scripted refund intent reaches different deterministic rules depending
    # on stored account facts. Renewal and handoff remain explicit separate branches.
    workflow.extractor.plan = ExtractedPlan(intent="refund", reason_code="duplicate_charge")
    session, headers = start(api, "p_duplicate")
    assert chat(api, session, headers).json()["actions"][0]["status"] == "executed"
    session, headers = start(api, "p_cancel")
    assert chat(api, session, headers).json()["actions"][0]["reason_code"] == "REFUND_WINDOW_EXPIRED"
    workflow.extractor.plan = ExtractedPlan(intent="renewal", cancel_at_period_end=True)
    assert "will not renew" in chat(api, session, headers, "Cancel renewal", "message-2").json()["response"]
    workflow.extractor.plan = ExtractedPlan(intent="handoff")
    result = chat(api, session, headers, "Human please", "message-3").json()
    assert result["escalated"] and result["handoff"]["id"]


def test_auth_scope_and_conversation_are_bound_by_server(api, workflow):
    # Exercise missing/forged credentials, a different customer's action, and a forged
    # conversation. Authenticating one session must not permit another session's data.
    a, ha = start(api)
    b, hb = start(api)
    action_id = chat(api, a, ha).json()["actions"][0]["id"]
    assert api.get("/customer/actions", headers=hb, params={"conv_id": a["conv_id"]}).status_code == 404
    assert chat(api, a, hb).status_code == 404
    assert api.post(f"/customer/demo/actions/{action_id}/decide", headers=hb,
                    json={"decision": "approve", "note": "fake admin"}).status_code == 404
    assert api.get("/admin/actions", headers=ha).status_code == 401
    assert api.get("/customer/actions", headers={"Authorization": "Bearer admin-test"},
                   params={"conv_id": a["conv_id"]}).status_code == 401
    assert chat(api, a, ha, user_id="another-customer").status_code == 422
    workflow.business.clock = lambda: 1_800_000_000 + 7201
    assert chat(api, a, ha).status_code == 401
    assert b["conv_id"] != a["conv_id"]


def test_idempotency_conflict_and_input_validation(api):
    # Identical retry IDs with changed messages conflict. Whitespace, excessive length,
    # and illegal request-ID characters are rejected before business execution.
    session, headers = start(api)
    assert chat(api, session, headers).status_code == 200
    assert chat(api, session, headers, message="Something else").status_code == 409
    assert chat(api, session, headers, message=" " * 4).status_code == 422
    assert chat(api, session, headers, message="x" * 4001).status_code == 422
    assert chat(api, session, headers, request_id="bad/id").status_code == 422


def test_demo_toggle_and_limits_are_enforced(api, workflow):
    # Test the self-approval gate separately from creation/message quotas. Replaying
    # an existing completed request remains legal even after the message limit is reached.
    workflow.sessions.sessions_per_ip_hour = 1
    session, headers = start(api)
    assert api.post("/customer/sessions", json={"persona_id": "p_b2b"}).status_code == 429
    workflow.sessions.max_messages = 1
    assert chat(api, session, headers).status_code == 200
    assert chat(api, session, headers, "Second message", "new").status_code == 429
    # Replaying a completed request is free even after the message cap.
    assert chat(api, session, headers).status_code == 200
    with workflow.business.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sandboxes").fetchone()[0] == 1
    app = FastAPI()
    app.include_router(create_business_router(lambda: workflow, lambda: "admin", lambda: False))
    action = workflow.business.list_actions()[0]
    assert TestClient(app).post(f"/customer/demo/actions/{action['id']}/decide", headers=headers,
                               json={"decision": "approve", "note": "test"}).status_code == 404


def test_inflight_lease_and_recovery_preserve_plan(workflow):
    # Move only the injected clock to expire a lease. Recovery may retry the original
    # message with its saved plan; a stale worker's old claim cannot replace that plan.
    created = workflow.sessions.create("p_b2b", "peer")
    session = workflow.sessions.authenticate(created["token"])
    state = workflow.sessions.begin(session, session.conv_id, "one", "Refund")
    with pytest.raises(BusinessError):
        workflow.sessions.begin(session, session.conv_id, "two", "Another message")
    with pytest.raises(BusinessError):
        workflow.sessions.begin(session, session.conv_id, "one", "Refund")
    plan = ExtractedPlan(intent="refund").model_dump()
    workflow.sessions.save_plan(session, "one", state["claim"], plan)
    workflow.business.clock = lambda: 1_800_000_000 + 121
    recovery = workflow.sessions.begin(session, session.conv_id, "one", "Refund")
    assert recovery["plan"] == plan
    with pytest.raises(BusinessError):
        workflow.sessions.save_plan(session, "one", state["claim"], {"intent": "renewal"})


@pytest.mark.asyncio
async def test_crash_after_side_effect_replays_saved_plan_and_operation(workflow, monkeypatch):
    # Break response persistence AFTER the domain commits a refund. Changing the fake
    # extractor afterwards must not change the saved intent or repeat extraction/effects.
    created = workflow.sessions.create("p_duplicate", "peer")
    session = workflow.sessions.authenticate(created["token"])
    workflow.extractor.plan = ExtractedPlan(intent="refund", reason_code="duplicate_charge")
    original = workflow.sessions.complete

    def crash(*_):
        raise RuntimeError("connection lost after refund committed")

    monkeypatch.setattr(workflow.sessions, "complete", crash)
    with pytest.raises(RuntimeError):
        await workflow.chat(session, "Refund duplicate", session.conv_id, "same-id")
    workflow.extractor.plan = ExtractedPlan(intent="renewal", cancel_at_period_end=True)
    monkeypatch.setattr(workflow.sessions, "complete", original)
    response = await workflow.chat(session, "Refund duplicate", session.conv_id, "same-id")
    assert response["intent"] == "refund"
    assert workflow.extractor.calls == 1
    with workflow.business.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 1
        assert conn.execute("SELECT message_count FROM customer_sessions").fetchone()[0] == 1
        assert created["token"] not in json.dumps(dict(conn.execute("SELECT * FROM customer_requests").fetchone()))


@pytest.mark.asyncio
async def test_extractor_failure_is_a_no_write_clarification(workflow):
    # A deliberately slow coroutine exceeds a short test deadline. Timeout must return
    # clarification and zero actions rather than guessing or defaulting to a refund.
    class SlowExtractor:
        async def extract(self, *_):
            await asyncio.sleep(10)

    workflow.extractor = SlowExtractor()
    workflow.model_timeout = 0.01
    created = workflow.sessions.create("p_duplicate", "peer")
    session = workflow.sessions.authenticate(created["token"])
    result = await workflow.chat(session, "Ambiguous", session.conv_id, "one")
    assert result["intent"] == "clarify"
    assert result["actions"] == []


def test_tokens_are_hashed_and_data_survives_service_recreation(workflow):
    # A fresh service instance shares persisted identity while the stored digest differs
    # from the raw bearer token. This tests database state, not an in-memory session map.
    created = workflow.sessions.create("p_b2b", "peer")
    with workflow.business.db.connect() as conn:
        assert conn.execute("SELECT token_hash FROM customer_sessions").fetchone()[0] != created["token"]
    replacement = SessionService(BusinessService(workflow.business.db, clock=workflow.business.clock))
    assert replacement.authenticate(created["token"]).conv_id == created["conv_id"]


def test_expired_admin_decision_commits_expiry_and_returns_conflict(api, workflow):
    # HTTP 409 is returned AFTER the domain persists expired. Check rows directly so
    # an implementation that accidentally rolled expiry back cannot pass by status alone.
    session, headers = start(api)
    action_id = chat(api, session, headers).json()["actions"][0]["id"]
    workflow.business.clock = lambda: 1_800_000_000 + 25 * 3600
    response = api.post(f"/admin/actions/{action_id}/decide", headers={"Authorization": "Bearer admin-test"},
                        json={"decision": "approve", "note": "Too late"})
    assert response.status_code == 409
    with workflow.business.db.connect() as conn:
        assert conn.execute("SELECT status FROM actions WHERE id=?", (action_id,)).fetchone()[0] == "expired"
        assert conn.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] == 0
