"""Check server grants, strict arguments and one-write-per-input independently of LLMs."""
import pytest

from tests.agent_fixtures import finish, runtime


@pytest.mark.parametrize("name,args,grant,expected", [
    ("request_refund", {"charge_id": "ch_2", "reason_code": "duplicate_charge"}, {}, "CONSENT_REQUIRED"),
    ("request_refund", {"charge_id": "ch_1", "reason_code": "other"}, {"kind": "refund", "charge_id": "ch_2"}, "CONSENT_REQUIRED"),
    ("get_account", {"customer_id": "another"}, {}, "INVALID_ARGUMENTS"),
    ("set_renewal", {"cancel_at_period_end": "false"}, {}, "INVALID_ARGUMENTS"),
    ("approve_refund", {}, {}, "NOT_ALLOWED"),
])
async def test_gateway_rejects_ungranted_or_malformed_calls(tmp_path, name, args, grant, expected):
    engine, session, _ = await runtime(tmp_path, lambda *_: finish())
    try:
        row = engine.store.create(session, "r", "Research only. Do not refund.", grant=grant)
        version = engine.store.claim(row["id"], "test")
        result = await engine.gateway.execute(row, {"id": "one", "name": name, "input": args}, "test", version)
        assert result["error"] == expected
        assert engine.gateway.business.list_actions(session.scope, session.conv_id) == []
    finally:
        await engine.close()


async def test_replay_reuses_action_but_a_second_write_id_is_rejected(tmp_path):
    engine, session, _ = await runtime(tmp_path, lambda *_: finish())
    try:
        row = engine.store.create(session, "r", "Refund this whole payment", grant={"kind": "refund", "charge_id": "ch_2"})
        version = engine.store.claim(row["id"], "test")
        request = {"id": "one", "name": "request_refund", "input": {"charge_id": "ch_2", "reason_code": "duplicate_charge"}}
        first = await engine.gateway.execute(row, request, "test", version)
        assert first["action"]["status"] == "executed"
        assert await engine.gateway.execute(row, request, "test", version) == first
        second = await engine.gateway.execute(row, {**request, "id": "two"}, "test", version)
        assert second["error"] == "WRITE_LIMIT"
        assert len(engine.gateway.business.list_actions(session.scope, session.conv_id)) == 1
    finally:
        await engine.close()
