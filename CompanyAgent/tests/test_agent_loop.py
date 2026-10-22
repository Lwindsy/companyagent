"""Verify decisions depend on observations, and malformed protocols stop safely."""
import json

import pytest

from tests.agent_fixtures import call, finish, observations, runtime


@pytest.mark.parametrize("status,next_tool", [("degraded", "get_incident"), ("healthy", "search_knowledge")])
async def test_observation_changes_next_action(tmp_path, status, next_tool):
    # The initial goal/model are identical in both cases. Only the diagnostic
    # environment changes; the second call must consequently choose another tool.
    def decide(messages, tools, grant):
        seen = observations(messages)
        if not seen:
            return call("get_service_status")
        if len(seen) == 1:
            return call("get_incident", {"incident_id": seen[0]["incident_id"]}, "next") if seen[0]["status"] == "degraded" else call("search_knowledge", {"query": "login 401"}, "next")
        return finish("Checked the returned evidence.", ["service:status"])
    engine, session, _ = await runtime(tmp_path, decide)
    try:
        row = engine.store.create(session, "request", "Investigate login 401. Do not change my subscription.")
        with engine.store.db.transaction() as conn:
            conn.execute("UPDATE agent_runs SET payload=json_set(payload,'$.diagnostic',?) WHERE id=?", (status, row["id"]))
        result = await engine.drive(row["id"])
        assert result["status"] == "completed"
        names = [e["data"]["name"] for e in engine.store.events(row["id"]) if e["kind"] == "tool_started"]
        assert names == ["get_service_status", next_tool]
        assert len(engine.model.calls) == 3
    finally:
        await engine.close()


@pytest.mark.parametrize("reply,code", [
    ({"content": [], "stop_reason": "max_tokens", "cost_micro": 0}, "MODEL_INCOMPLETE"),
    (finish(evidence=["invented:source"]), "INVALID_FINAL"),
    ({"content": [{"type": "text", "text": "not JSON"}], "stop_reason": "end_turn", "cost_micro": 0}, "INVALID_FINAL"),
])
async def test_invalid_outputs_do_not_become_business_success(tmp_path, reply, code):
    engine, session, _ = await runtime(tmp_path, lambda *_: reply)
    try:
        row = engine.store.create(session, "r", "Check my account")
        result = await engine.drive(row["id"])
        assert result["status"] == "failed" and result["result"]["error"] == code
        assert engine.gateway.business.list_actions(session.scope, session.conv_id) == []
    finally:
        await engine.close()


async def test_repeated_reads_stop_without_an_infinite_loop(tmp_path):
    engine, session, _ = await runtime(tmp_path, lambda messages, *_: call("get_account", identity=f"read-{len(messages)}"))
    try:
        row = engine.store.create(session, "r", "Check account")
        result = await engine.drive(row["id"])
        assert result["result"]["error"] == "STEP_LIMIT"
        assert len(engine.model.calls) == 4
    finally:
        await engine.close()


async def test_native_blocks_and_call_pairing_survive_checkpoint(tmp_path):
    # A provider-owned signature must not be lost by reducing the response to text.
    def decide(messages, *_):
        if len(messages) == 1:
            reply = call("get_account")
            reply["content"].insert(0, {"type": "thinking", "thinking": "test-only placeholder", "signature": "opaque-test"})
            return reply
        assert messages[-2]["content"][0]["signature"] == "opaque-test"
        assert messages[-1]["content"][0]["tool_use_id"] == "call-1"
        return finish(evidence=["account:current"])
    engine, session, _ = await runtime(tmp_path, decide)
    try:
        row = engine.store.create(session, "r", "Check account")
        assert (await engine.drive(row["id"]))["status"] == "completed"
        assert "opaque-test" not in json.dumps(engine.store.events(row["id"]))
    finally:
        await engine.close()
