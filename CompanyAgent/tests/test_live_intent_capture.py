"""Offline checks of spending and capture boundaries; never use a live model key.

These tests inject a tiny SDK-shaped object. They prove when a paid request would
be refused, what remains reserved after uncertainty, and what enters the prompt.
They do not present fabricated plans as real-model quality or real spending.
"""

from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from evaluation.capture_intent_predictions import BudgetStop, DollarLedger, MeteredMessages, capture
from evaluation.grade_intent_predictions import ROOT, fingerprints, load_dataset


class FakeSDK:
    """Record create calls and return a local reply or a supplied failure."""

    def __init__(self, text='{"intent":"charges"}', failure=None, model="test-model"):
        # messages=self exposes the same nested attribute that the real SDK offers.
        self.messages, self.calls = self, []
        self.text, self.failure, self.model = text, failure, model

    async def create(self, **kwargs):
        # Keep only synthetic inputs in memory; no HTTP or authentication is involved.
        self.calls.append(kwargs)
        if self.failure:
            raise self.failure
        return SimpleNamespace(model=self.model, stop_reason="end_turn",
                               content=[{"type": "text", "text": self.text}],
                               usage=SimpleNamespace(input_tokens=100, output_tokens=30))


def meter_for(sdk, tmp_path, limit="1"):
    # Dollars use Decimal strings, avoiding floating-point rounding at the ceiling.
    ledger = DollarLedger(tmp_path / "dollars.json", Decimal(limit))
    return MeteredMessages(sdk, ledger, Decimal("2"), Decimal("10"))


@pytest.mark.asyncio
async def test_insufficient_budget_refuses_before_sdk_and_does_not_mutate_balance(tmp_path):
    # An allowance too small for one worst-case response must cause zero paid calls.
    sdk = FakeSDK()
    meter = meter_for(sdk, tmp_path, "0.000001")
    try:
        with pytest.raises(BudgetStop):
            await meter.create(model="test-model", system="test", messages=[], max_tokens=512)
        assert sdk.calls == [] and meter.ledger.used == 0
    finally:
        meter.ledger.close()


@pytest.mark.asyncio
async def test_uncertain_failure_retains_hold_across_restart_and_locks_other_capture(tmp_path):
    # A timeout does not prove the provider spent nothing; keep the whole hold.
    sdk = FakeSDK(failure=TimeoutError())
    meter = meter_for(sdk, tmp_path)
    with pytest.raises(FileExistsError):
        DollarLedger(tmp_path / "dollars.json", Decimal("1"))
    try:
        with pytest.raises(TimeoutError):
            await meter.create(model="test-model", system="test", messages=[], max_tokens=512)
        held = meter.ledger.used
        assert held > 0 and len(sdk.calls) == 1
    finally:
        meter.ledger.close()
    restarted = DollarLedger(tmp_path / "dollars.json", Decimal("1"))
    try:
        assert restarted.used == held
    finally:
        restarted.close()


@pytest.mark.asyncio
async def test_capture_excludes_labels_accounts_invalid_json_and_disables_server_fallback(tmp_path):
    # Invalid JSON is still a charged response and a failed answer, never a free
    # synthetic clarification. Only the first fictional dataset case is requested.
    sdk = FakeSDK(text="not JSON")
    meter = meter_for(sdk, tmp_path)
    dataset_path = ROOT / "evaluation/datasets/intent_cases.json"
    dataset = SimpleNamespace(cases=load_dataset(dataset_path).cases[:1])
    output, evidence = tmp_path / "capture.json", tmp_path / "evidence.json"
    try:
        await capture(sdk, dataset, "test-model", meter, output, evidence, fingerprints(dataset_path))
        saved = json.loads(output.read_text(encoding="utf-8"))
        assert saved["source"] == "external_capture" and saved["predictions"][0]["plan"] is None
        sent = json.loads(sdk.calls[0]["messages"][0]["content"])
        assert set(sent) == {"current_customer_message", "recent_turns", "account_reference_data"}
        assert "expected" not in sent and "rationale" not in sent
        assert "fallbacks" not in sdk.calls[0]["extra_body"]
        assert meter.ledger.used == Decimal("0.0005")
        assert json.loads(evidence.read_text(encoding="utf-8"))["calls"][0]["parse_error"] == "JSONDecodeError"
    finally:
        meter.ledger.close()


@pytest.mark.asyncio
async def test_different_reported_model_keeps_reservation_and_stops(tmp_path):
    # Never settle at a cheap requested rate when the server reports another model.
    sdk = FakeSDK(model="different-expensive-model")
    meter = meter_for(sdk, tmp_path)
    try:
        with pytest.raises(BudgetStop):
            await meter.create(model="test-model", system="test", messages=[], max_tokens=512,
                               extra_body={"fallbacks": "default"},
                               extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"})
        assert meter.ledger.used > Decimal("0.0005")
        assert sdk.calls[0]["extra_body"] == {} and sdk.calls[0]["extra_headers"] == {}
    finally:
        meter.ledger.close()


def test_ledger_cannot_change_ceiling_or_accept_negative_balance(tmp_path):
    # A later run must share the same ceiling; corrupt data must not create credit.
    path = tmp_path / "dollars.json"
    path.write_text('{"limit_usd":"1","used_or_reserved_usd":"0.1"}', encoding="utf-8")
    with pytest.raises(ValueError):
        DollarLedger(path, Decimal("2"))
    path.write_text('{"limit_usd":"1","used_or_reserved_usd":"-1"}', encoding="utf-8")
    with pytest.raises(ValueError):
        DollarLedger(path, Decimal("1"))
