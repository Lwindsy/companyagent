"""Real SQLite transactions, including separate-connection races. No network services."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from business.db import Database
from business.models import BusinessError, PolicyConfig, Scope, refund_policy
from business.service import BusinessService


@pytest.fixture
def business(tmp_path):
    # pytest provides a disposable directory per test. Nothing here opens the deployed
    # database or calls a real payment API. A fixed clock keeps relative story ages stable.
    db = Database(str(tmp_path / "business.db"))
    db.initialize()
    db.initialize()  # Reopening an existing schema is safe.
    return BusinessService(db, clock=lambda: 1_800_000_000)


def refund(business, scope, op="op-1", charge="ch_2", reason="duplicate_charge"):
    # Supply the same trusted IDs a workflow would pass. Keeping defaults makes replay
    # explicit: omit op for a retry; choose another op to model a genuinely new request.
    return business.execute(scope, "conversation", op, "refund", {"charge_id": charge, "reason_code": reason})


def test_duplicate_full_refund_and_operation_replay(business):
    # First execution creates a refund; same-ID replay returns exactly that action.
    # A new request for the charge is denied, while altering an existing ID is a conflict.
    scope, _ = business.create_sandbox("p_duplicate")
    action = refund(business, scope)
    assert action["status"] == "executed"
    assert action["reason_code"] == "VERIFIED_DUPLICATE"
    assert refund(business, scope) == action
    assert [r["refunded"] for r in business.overview(scope)["charges"]] == [0, 1]
    assert refund(business, scope, "op-2")["status"] == "denied"
    with pytest.raises(BusinessError, match="different arguments"):
        refund(business, scope, charge="ch_1")


def test_original_charge_cannot_be_auto_refunded_as_duplicate(business):
    # The customer's "duplicate" label is not evidence. Only the later matched charge
    # qualifies automatically; the original payment instead needs supervisor review.
    scope, _ = business.create_sandbox("p_duplicate")
    assert refund(business, scope, charge="ch_1")["status"] == "pending"


def test_scope_is_enforced_for_reads_writes_and_demo_decisions(business):
    # Create two copies of the same persona to prove identical customer/charge IDs
    # do not permit crossing sandbox ownership. Foreign-owned actions look not found.
    a, _ = business.create_sandbox("p_b2b")
    b, _ = business.create_sandbox("p_b2b")
    action = refund(business, a, charge="ch_1", reason="other")
    assert business.list_actions(b) == []
    with pytest.raises(BusinessError) as error:
        business.decide(action["id"], "approve", "demo", "checked", scope=b)
    assert error.value.status == 404
    with pytest.raises(BusinessError) as error:
        refund(business, Scope(a.sandbox_id, b.sandbox_id))
    assert error.value.status == 404


def test_high_value_requires_note_and_approval_rechecks_without_rejecting_itself(business):
    # The 450 EUR story requires review. Approval needs a note and must exclude its
    # own pending action from duplicate detection; a second approval cannot execute again.
    scope, _ = business.create_sandbox("p_b2b")
    action = refund(business, scope, charge="ch_1", reason="other")
    assert action["status"] == "pending"
    with pytest.raises(BusinessError) as error:
        business.decide(action["id"], "approve", "admin")
    assert error.value.code == "NOTE_REQUIRED"
    assert business.decide(action["id"], "approve", "admin", "Verified customer request")["status"] == "executed"
    with pytest.raises(BusinessError) as error:
        business.decide(action["id"], "approve", "admin", "again")
    assert error.value.code == "ALREADY_DECIDED"


def test_over_window_refund_denied_but_renewal_remains_available(business):
    # Expired refund eligibility does not prevent renewal changes. Every new toggle
    # has its own operation ID; repeating the current target is a successful no-op.
    scope, _ = business.create_sandbox("p_cancel")
    action = refund(business, scope, charge="ch_1", reason="other")
    assert (action["status"], action["reason_code"]) == ("denied", "REFUND_WINDOW_EXPIRED")
    states = [True, False, True, True]
    for i, value in enumerate(states):
        assert business.execute(scope, "conversation", f"toggle-{i}", "renewal", {"cancel_at_period_end": value})["status"] == "executed"
        assert business.overview(scope)["cancel_at_period_end"] is value
    with business.db.connect() as conn:
        assert conn.execute("SELECT result_json FROM actions ORDER BY rowid DESC LIMIT 1").fetchone()[0] == '{"changed": false}'


def test_concurrent_auto_refunds_only_create_one_refund(business):
    # Two actual threads use separate SQLite connections, not mocked function calls.
    # Different IDs bypass request replay but must still yield exactly one refund row.
    scope, _ = business.create_sandbox("p_duplicate")
    with ThreadPoolExecutor(2) as pool:
        actions = list(pool.map(lambda op: refund(business, scope, op), ["first", "second"]))
    assert sorted(a["status"] for a in actions) == ["denied", "executed"]
    with business.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] == 1


def test_concurrent_admin_decisions_only_execute_once(business):
    # Race two supervisors against one pending action. The transaction must let only
    # one execute; the other sees the updated state and reports ALREADY_DECIDED.
    scope, _ = business.create_sandbox("p_b2b")
    action = refund(business, scope, charge="ch_1", reason="other")

    def approve(_):
        try:
            return business.decide(action["id"], "approve", "admin", "Verified")["status"]
        except BusinessError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(approve, range(2))) == ["ALREADY_DECIDED", "executed"]
    with business.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] == 1


def test_mid_approval_exception_rolls_back_business_state_and_events(business, monkeypatch):
    # Inject a failure AFTER the real refund insert but BEFORE the outer commit.
    # This checks atomic rollback, not merely an early validation failure.
    scope, _ = business.create_sandbox("p_b2b")
    action = refund(business, scope, charge="ch_1", reason="other")
    original = business._apply

    def crash(*args):
        # *args forwards the original arguments unchanged, then deliberately breaks
        # the transaction. monkeypatch restores the normal method when the test ends.
        original(*args)
        raise RuntimeError("simulated process failure before commit")

    monkeypatch.setattr(business, "_apply", crash)
    with pytest.raises(RuntimeError):
        business.decide(action["id"], "approve", "admin", "Verified")
    with business.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert business.list_actions(scope)[0]["status"] == "pending"
    # After rollback, the pending action should still be safely approvable on retry.
    monkeypatch.setattr(business, "_apply", original)
    assert business.decide(action["id"], "approve", "admin", "Retry")["status"] == "executed"


def test_approval_rechecks_current_window(business):
    # replace creates another frozen config; it does not mutate fields in the old one.
    # Advance the injected clock past eligibility while keeping the approval alive.
    business.config = replace(business.config, approval_ttl_hours=48)
    scope, _ = business.create_sandbox("p_b2b")
    with business.db.transaction() as conn:
        conn.execute("UPDATE charges SET created_at=?", (int(business.clock()) - 30 * 86400 + 3600,))
    action = refund(business, scope, charge="ch_1", reason="other")
    business.clock = lambda: 1_800_000_000 + 7200
    failed = business.decide(action["id"], "approve", "admin", "Verified")
    assert (failed["status"], failed["reason_code"]) == ("failed", "REFUND_WINDOW_EXPIRED")


def test_rejection_expiry_and_handoff_are_persistent(business):
    # Verify rejection notes, lazily persisted expiry, and open-ticket reuse. The old
    # response remains a snapshot; querying fresh state is necessary after time advances.
    scope, _ = business.create_sandbox("p_b2b")
    action = refund(business, scope, charge="ch_1", reason="other")
    with pytest.raises(BusinessError):
        business.decide(action["id"], "reject", "admin")
    assert business.decide(action["id"], "reject", "admin", "Need more evidence")["status"] == "rejected"
    another = refund(business, scope, "new-request", charge="ch_1", reason="other")
    business.clock = lambda: 1_800_000_000 + 25 * 3600
    assert business.list_actions(scope)[0]["status"] == "expired"
    assert another["status"] == "pending"  # Previous response is a snapshot.
    assert business.handoff(scope, "conversation", "Customer requests an exception") == business.handoff(scope, "conversation", "Repeated")


def test_persistent_write_limit_and_invalid_arguments(business):
    # Count persisted action attempts, including denial, and reject a string pretending
    # to be a boolean. Neither quota nor typing depends on the frontend behaving correctly.
    business.config = replace(business.config, max_writes_per_conversation=1)
    scope, _ = business.create_sandbox("p_cancel")
    refund(business, scope, charge="ch_1", reason="other")
    with pytest.raises(BusinessError) as error:
        business.execute(scope, "conversation", "new", "renewal", {"cancel_at_period_end": True})
    assert error.value.code == "WRITE_LIMIT"
    with pytest.raises(BusinessError):
        business.execute(scope, "conversation", "bad", "renewal", {"cancel_at_period_end": "true"})


@pytest.mark.parametrize("changes,reason", [
    ({"amount_cents": 0}, "INVALID_AMOUNT"),
    ({"age_seconds": -1}, "REFUND_WINDOW_EXPIRED"),
    ({"monthly_total": 49999}, "REFUND_CAP_REACHED"),
    ({"refunded": True}, "REFUND_ALREADY_REQUESTED"),
    ({"pending_other": True}, "REFUND_ALREADY_REQUESTED"),
])
def test_approval_never_overrides_hard_rules(changes, reason):
    # Parametrization reruns this function with each invalid fact. Even approved=True
    # must not override a bad amount, time window, duplicate, or account cap.
    facts = dict(age_seconds=0, amount_cents=3990, refunded=False, pending_other=False,
                 monthly_total=0, verified_duplicate=False, approved=True, config=PolicyConfig())
    assert refund_policy(**{**facts, **changes}).reason == reason
