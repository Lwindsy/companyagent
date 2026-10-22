"""Verify holds occur before calls, survive uncertainty and are not reset on restart."""
from tests.agent_fixtures import finish, runtime


async def test_budget_block_happens_before_network_or_model(tmp_path):
    engine, session, _ = await runtime(tmp_path, lambda *_: finish())
    try:
        engine.model.reservation = lambda *_: 101
        row = engine.store.create(session, "r", "Check", limit_micro=100)
        result = await engine.drive(row["id"])
        assert result["status"] == "budget_exhausted"
        assert engine.model.calls == []
    finally:
        await engine.close()


async def test_uncertain_model_holds_money_and_is_not_reissued(tmp_path):
    engine, session, _ = await runtime(tmp_path, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    try:
        engine.model.reservation = lambda *_: 100
        row = engine.store.create(session, "r", "Check", limit_micro=1000)
        result = await engine.drive(row["id"])
        assert result["status"] == "failed"
        assert result["used_or_reserved_usd"] == 0.0001
        await engine.drive(row["id"])
        assert len(engine.model.calls) == 1
    finally:
        await engine.close()
