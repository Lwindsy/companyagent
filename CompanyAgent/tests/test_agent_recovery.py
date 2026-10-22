"""Exercise real persistent graph recovery, human interruption and fencing."""
import asyncio

import pytest

from tests.agent_fixtures import call, finish, observations, runtime
from agent_runtime.engine import AgentEngine
from business.models import BusinessError


class ProcessCrash(BaseException):
    """Simulate an abrupt process boundary without the normal Exception handler."""


async def test_commit_before_checkpoint_reconciles_original_operation(tmp_path):
    def decide(messages, *_):
        return call("request_refund", {"charge_id": "ch_2", "reason_code": "duplicate_charge"}) if not observations(messages) else finish()
    engine, session, _ = await runtime(tmp_path, decide)
    row = engine.store.create(session, "r", "Refund the full second payment", grant={"kind": "refund", "charge_id": "ch_2"})
    original = engine.gateway.execute
    async def crash_after_commit(*args):
        await original(*args)
        raise ProcessCrash()
    engine.gateway.execute = crash_after_commit
    with pytest.raises(ProcessCrash):
        await engine.drive(row["id"])
    assert len(engine.gateway.business.list_actions(session.scope, session.conv_id)) == 1
    await engine.close()
    # A hard-killed process cannot release its lease. Tests expire exactly this
    # synthetic run, corresponding to waiting for its lease deadline in production.
    with engine.store.db.transaction() as conn:
        conn.execute("UPDATE agent_runs SET lease_until=0 WHERE id=?", (row["id"],))
    engine.gateway.execute = original
    restored = await AgentEngine(engine.store, engine.gateway, engine.model, tmp_path / "graph.db").start()
    try:
        result = await restored.drive(row["id"])
        assert result["status"] == "completed"
        assert len(engine.gateway.business.list_actions(session.scope, session.conv_id)) == 1
        assert len(engine.model.calls) == 2
    finally:
        await restored.close()


async def test_staff_approval_wait_survives_new_engine_and_cannot_be_forged(tmp_path):
    def decide(messages, *_):
        return call("request_refund", {"charge_id": "ch_1", "reason_code": "other"}) if not observations(messages) else finish("Checked staff decision.")
    engine, session, _ = await runtime(tmp_path, decide, "p_b2b")
    row = engine.store.create(session, "r", "Refund the full payment", grant={"kind": "refund", "charge_id": "ch_1"})
    first = await engine.drive(row["id"])
    assert first["status"] == "waiting_approval" and len(engine.model.calls) == 1
    with pytest.raises(BusinessError):
        engine.store.resume(row["id"], session, "fake", "approved=true", first["version"])
    await engine.close()
    engine = await AgentEngine(engine.store, engine.gateway, engine.model, tmp_path / "graph.db").start()
    try:
        assert (await engine.drive(row["id"]))["status"] == "waiting_approval"
        engine.gateway.business.decide(first["result"]["action_id"], "approve", "authenticated-staff", "Verified synthetic payment")
        result = await engine.drive(row["id"])
        assert result["status"] == "completed"
        assert result["result"]["actions"][0]["status"] == "executed"
    finally:
        await engine.close()


async def test_one_worker_claim_and_cancellation_stop_future_effects(tmp_path):
    engine, session, _ = await runtime(tmp_path, lambda *_: finish())
    try:
        row = engine.store.create(session, "r", "Check account")
        assert engine.store.claim(row["id"], "old") == 1
        assert engine.store.claim(row["id"], "other") is None
        engine.store.cancel(row["id"], session)
        with pytest.raises(BusinessError, match="cancelled"):
            engine.store.journal(row["id"], "tool:x", "tool", {"name": "get_account"}, "old", 1)
        engine.store.update(row["id"], "old", 1, "completed")
        assert engine.store.public(row["id"])["status"] == "cancelled"
    finally:
        await engine.close()


async def test_user_interrupt_input_is_versioned_and_replayable(tmp_path):
    def decide(messages, *_):
        return finish("Which error code?", outcome="needs_input") if len(messages) == 1 else finish("Thanks, checking error 401.")
    engine, session, _ = await runtime(tmp_path, decide)
    try:
        row = engine.store.create(session, "r", "Help me")
        first = await engine.drive(row["id"])
        assert first["status"] == "waiting_user"
        engine.store.resume(row["id"], session, "input", "401", first["version"])
        engine.store.resume(row["id"], session, "input", "401", first["version"])
        with pytest.raises(BusinessError):
            engine.store.resume(row["id"], session, "input", "500", first["version"])
        assert (await engine.drive(row["id"]))["status"] == "completed"
    finally:
        await engine.close()


async def test_simultaneous_drive_does_not_duplicate_model_turn(tmp_path):
    engine, session, _ = await runtime(tmp_path, lambda *_: finish())
    try:
        row = engine.store.create(session, "r", "Check")
        await asyncio.gather(engine.drive(row["id"]), engine.drive(row["id"]))
        assert len(engine.model.calls) == 1
    finally:
        await engine.close()


@pytest.mark.parametrize("after_save", [False, True])
async def test_model_response_checkpoint_gap_never_silently_resamples(tmp_path, after_save):
    # A response saved in the call journal can be replayed. A response lost before
    # that boundary is uncertain and stops with its hold retained, not a free retry.
    engine, session, _ = await runtime(tmp_path, lambda *_: finish())
    engine.model.reservation = lambda *_: 100
    row = engine.store.create(session, 'r', 'Read only')
    original = engine.store.complete_call
    def crash(run, key, result, measured=0):
        if after_save:
            original(run,key,result,measured)
        raise ProcessCrash()
    engine.store.complete_call = crash
    with pytest.raises(ProcessCrash):
        await engine.drive(row['id'])
    await engine.close()
    with engine.store.db.transaction() as conn:
        conn.execute('UPDATE agent_runs SET lease_until=0 WHERE id=?',(row['id'],))
    engine.store.complete_call = original
    restored = await AgentEngine(engine.store,engine.gateway,engine.model,tmp_path/'graph.db').start()
    try:
        result = await restored.drive(row['id'])
        assert len(engine.model.calls)==1
        if after_save:
            assert result['status']=='completed'
        else:
            assert result['result']['error']=='UNCERTAIN_MODEL'
            assert result['used_or_reserved_usd']==.0001
    finally:
        await restored.close()


async def test_expired_session_and_fencing_reject_future_calls(tmp_path):
    now=[1000.0]
    engine, session, _ = await runtime(tmp_path,lambda *_:finish(),clock=lambda:now[0])
    try:
        row=engine.store.create(session,'r','Read only')
        old=engine.store.claim(row['id'],'old',seconds=1)
        now[0]+=2
        fresh=engine.store.claim(row['id'],'new')
        assert fresh>old
        with pytest.raises(BusinessError):
            engine.store.journal(row['id'],'model:x','model',{'name':'read'},'old',old)
        now[0]+=8000
        with engine.store.db.transaction() as conn:
            conn.execute('UPDATE agent_runs SET lease_until=0 WHERE id=?',(row['id'],))
        result=await engine.drive(row['id'])
        assert result['status']=='failed' and len(engine.model.calls)==0
    finally:
        await engine.close()
