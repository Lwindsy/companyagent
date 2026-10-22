import json

import httpx
import pytest
from anthropic import AsyncAnthropic
from fastapi.testclient import TestClient

from api import main
from business.db import Database
from business.service import BusinessService
from workflows.anthropic_extractor import AnthropicExtractor, SYSTEM, _parse_plan
from workflows.models import ExtractedPlan
from workflows.service import WorkflowService


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "```\n{}\n```"])
def test_complete_json_or_single_outer_fence_preserves_refund_fields(wrapper):
    # A whole optional fence is formatting, not permission. Refund targets/reasons
    # must survive parsing, while a renewal-only flag must not leak into this branch.
    text = '{"intent":"refund","charge_id":"ch_2","reason_code":"duplicate_charge","cancel_at_period_end":true}'
    plan = _parse_plan(wrapper.replace("{}", text))
    assert plan.intent == "refund" and plan.charge_id == "ch_2"
    assert plan.reason_code == "duplicate_charge" and plan.cancel_at_period_end is None


@pytest.mark.parametrize("text", [
    'Explanation: {"intent":"refund"}',
    '```json\n{"intent":"refund"}\n```\nextra prose',
    '{"intent":"clarify","intent":"refund"}',
    '{"intent":"refund"}{"intent":"clarify"}',
    '```json\n{"intent":"refund","customer_id":"other"}\n```',
])
def test_format_tolerance_never_extracts_prose_or_hides_ambiguous_or_forbidden_data(text):
    # Reject complete invalid output rather than selecting one convenient substring.
    # ValueError includes Pydantic's validation failures for forbidden identity fields.
    with pytest.raises(ValueError):
        _parse_plan(text)


def test_status_plan_clears_unused_write_fields_and_partial_refund_rule_is_explicit():
    # Mentioning a target in status context must not look like a new refund proposal.
    # The prompt assertion preserves a required policy instruction; real semantic
    # success is checked separately by the saved live-corpus capture, not this string.
    plan = _parse_plan('{"intent":"status","charge_id":"ch_2","reason_code":"duplicate_charge","cancel_at_period_end":true}')
    assert plan.model_dump() == {"intent": "status", "charge_id": None, "reason_code": "other", "cancel_at_period_end": None}
    assert "partial refund" in SYSTEM and "Never discard an amount restriction" in SYSTEM


@pytest.mark.asyncio
async def test_verified_sonnet_family_sends_json_grammar_without_losing_effort_or_local_limits():
    # SDK 0.40 accepts newer wire fields through extra_body. The transport replacement
    # proves the format actually enters HTTP JSON instead of merely existing in Python.
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "msg-format", "type": "message", "role": "assistant",
                                         "model": "claude-sonnet-5-5", "stop_reason": "end_turn",
                                         "content": [{"type": "text", "text": '{"intent":"status"}'}],
                                         "usage": {"input_tokens": 100, "output_tokens": 30}})

    async with AsyncAnthropic(api_key="test", base_url="http://llm.test", max_retries=0,
                              http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as client:
        plan = await AnthropicExtractor(client, "claude-sonnet-5-5").extract("Status?", [], {})
    config = calls[0]["output_config"]
    assert len(calls) == 1 and plan.intent == "status"
    assert config["effort"] and config["format"]["type"] == "json_schema"
    schema = config["format"]["schema"]
    assert set(schema["required"]) == set(schema["properties"]) and not schema["additionalProperties"]
    assert "maxLength" not in schema["properties"]["charge_id"]["anyOf"][0]
    with pytest.raises(ValueError):
        _parse_plan(json.dumps({"intent": "refund", "charge_id": "x" * 81}))


@pytest.mark.asyncio
async def test_real_sdk_adapter_sends_one_untrusted_json_payload_and_no_tools():
    # Use the installed SDK but intercept HTTP with MockTransport. The "test" key and
    # .test hostname are fixtures; no real model request is sent or billed.
    # Assert the wire payload and call count, not real natural-language understanding.
    calls = []

    def handler(request):
        # Capture outgoing JSON, then return a synthetic SDK-compatible message object.
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={"id": "msg-extract", "type": "message", "role": "assistant", "model": "test-model",
                                         "content": [{"type": "text", "text": '{"intent":"refund","reason_code":"duplicate_charge"}'}],
                                         "stop_reason": "end_turn", "usage": {"input_tokens": 50, "output_tokens": 20}})

    async with AsyncAnthropic(api_key="test", base_url="http://llm.test", max_retries=0,
                              http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as client:
        result = await AnthropicExtractor(client, "test-model").extract("Refund the duplicate", [], {"charges": []})
    assert result.intent == "refund"
    assert len(calls) == 1
    assert "tools" not in calls[0]
    assert json.loads(calls[0]["messages"][0]["content"])["current_customer_message"] == "Refund the duplicate"
    assert "untrusted" in SYSTEM


@pytest.mark.asyncio
@pytest.mark.parametrize("text,stop", [('not json', "end_turn"), ('{"intent":"refund"}', "max_tokens"),
                                        ('{"intent":"approve","customer_id":"other"}', "end_turn")])
async def test_invalid_or_truncated_model_output_never_dispatches_a_write(tmp_path, text, stop):
    # Parametrized bad JSON, truncated output, and forbidden fields must all end in
    # clarification with no business action. SDK-shaped stubs isolate this failure path.
    class Messages:
        async def create(self, **_):
            from types import SimpleNamespace
            return SimpleNamespace(stop_reason=stop, content=[{"type": "text", "text": text}])

    class Client:
        messages = Messages()

    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    workflow = WorkflowService(BusinessService(db), AnthropicExtractor(Client(), "test-model"))
    created = workflow.sessions.create("p_b2b", "peer")
    session = workflow.sessions.authenticate(created["token"])
    result = await workflow.chat(session, "Refund", session.conv_id, "one")
    assert result["intent"] == "clarify" and result["actions"] == []


def test_main_chat_business_path_ignores_body_identity_and_never_falls_back_on_bad_token(tmp_path, monkeypatch):
    # Mount the actual main app without starting external-service lifespan. A forged
    # user_id cannot override the authenticated session, and invalid auth must fail
    # instead of reaching anonymous memory. monkeypatch restores environment/global state.
    class Fixed:
        async def extract(self, *_):
            return ExtractedPlan(intent="refund", reason_code="duplicate_charge")

    db = Database(str(tmp_path / "main.db"))
    db.initialize()
    workflow = WorkflowService(BusinessService(db), Fixed())
    monkeypatch.setenv("BUSINESS_WORKFLOWS_ENABLED", "true")
    monkeypatch.setattr(main, "_business_workflow", workflow)
    monkeypatch.setattr(main, "_memory", None)
    client = TestClient(main.app)  # Deliberately do not start Redis/Chroma lifespan.
    created = workflow.sessions.create("p_duplicate", "peer")
    body = {"message": "Refund duplicate", "conv_id": created["conv_id"], "request_id": "one", "user_id": "forged"}
    headers = {"Authorization": "Bearer " + created["token"]}
    response = client.post("/chat", json=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["mode"] == "business_workflow"
    assert response.json()["actions"][0]["status"] == "executed"
    assert client.post("/chat", json=body, headers={"Authorization": "Bearer bad-token"}).status_code == 401
    assert client.post("/chat", json=body, headers={"Authorization": "Basic invalid"}).status_code == 401
    assert client.post("/chat", json=body).status_code == 503  # Old path still requires old services.
    monkeypatch.setenv("BUSINESS_WORKFLOWS_ENABLED", "false")
    assert client.post("/chat", json=body, headers=headers).status_code == 404


def test_capabilities_are_dynamic_and_old_backends_need_no_new_contract(monkeypatch):
    # Capability flags follow initialization plus switches at request time. Absence
    # means optional controls stay hidden; it does not require Java/.NET to add new APIs.
    from types import SimpleNamespace
    monkeypatch.setattr(main, "_orchestrator", SimpleNamespace(get_stats=lambda: {}))
    monkeypatch.setattr(main, "_business_workflow", object())
    monkeypatch.setenv("BUSINESS_WORKFLOWS_ENABLED", "true")
    monkeypatch.setenv("DEMO_SELF_APPROVAL", "true")
    client = TestClient(main.app)
    assert client.get("/health").json()["capabilities"] == ["business_workflows", "approvals", "demo_self_approval"]
    monkeypatch.setenv("BUSINESS_WORKFLOWS_ENABLED", "false")
    assert client.get("/health").json()["capabilities"] == []
