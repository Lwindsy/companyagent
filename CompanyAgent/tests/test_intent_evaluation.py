"""Adversarial checks of the offline grader, not a real-model quality claim.

Every answer is fabricated locally. Tests deliberately submit wrong, missing and
malformed plans to prove that a plausible report cannot hide a grading failure.
The existing application conftest uses only synthetic credentials and transports.
"""

import copy
import json
import sys

import pytest
from pydantic import ValidationError

from evaluation.grade_intent_predictions import (
    PredictionFile,
    ROOT,
    fingerprints,
    grade,
    load_dataset,
    main,
    read_json,
)


# The shipped corpus is fictional and fixed. Deriving perfect answers from labels
# is useful ONLY to test grading mechanics, and is explicitly marked synthetic.
DATASET = ROOT / "evaluation/datasets/intent_cases.json"


def fixture_capture(dataset, hashes):
    """Return synthetic perfect predictions; never present them as model output."""
    return {"source": "synthetic_fixture", "model": "no-model-fixture", **hashes,
            "predictions": [{"id": case.id, "plan": case.expected.model_dump()} for case in dataset.cases]}


def test_reviewed_corpus_covers_language_risks_and_perfect_fixture_is_labeled():
    # First validate the actual delivered corpus. Category assertions preserve the
    # intended risk coverage even if someone later removes cases to improve a score.
    dataset = load_dataset(DATASET)
    tags = {tag for case in dataset.cases for tag in case.tags}
    assert {"negation", "multi_intent", "context", "injection", "authorization", "multilingual", "unsupported"} <= tags
    hashes = fingerprints(DATASET)
    report = grade(dataset, PredictionFile.model_validate(fixture_capture(dataset, hashes)), hashes)
    assert report["passed"] == report["total"] == 36
    assert report["declared_source"] == "synthetic_fixture"
    assert report["model_execution_verified"] is False


def test_missing_invalid_and_semantically_wrong_answers_all_fail():
    # One missing case, one extra identity field, and one valid-but-unsafe refund
    # must produce three failures. A schema-valid refund is not language correctness.
    dataset = load_dataset(DATASET)
    hashes = fingerprints(DATASET)
    capture = fixture_capture(dataset, hashes)
    capture["predictions"] = [row for row in capture["predictions"] if row["id"] != "list_en"]
    for row in capture["predictions"]:
        if row["id"] == "approve_claim":
            row["plan"] = {"intent": "refund", "customer_id": "forged"}
        if row["id"] == "negated_refund":
            row["plan"] = {"intent": "refund"}
    report = grade(dataset, PredictionFile.model_validate(capture), hashes)
    failures = {row["id"]: row for row in report["cases"] if not row["passed"]}
    assert report["passed"] == 33 and report["provided"] == 35
    assert failures["list_en"]["failure"] == "missing_prediction"
    assert failures["approve_claim"]["failure"] == "invalid_plan"
    assert failures["negated_refund"]["mismatched_fields"] == ["intent"]
    assert report["unsafe_write_proposals"] == 1
    assert report["categories"]["negation"]["unsafe_write_proposals"] == 1


@pytest.mark.parametrize("field,value", [("charge_id", "c_first"), ("reason_code", "duplicate_charge")])
def test_right_intent_with_wrong_target_or_reason_fails(field, value):
    # Matching the intent name alone is insufficient: the wrong payment or refund
    # reason can change business execution even though every field is well typed.
    dataset = load_dataset(DATASET)
    hashes = fingerprints(DATASET)
    capture = fixture_capture(dataset, hashes)
    row = next(row for row in capture["predictions"] if row["id"] == "refund_named")
    row["plan"][field] = value
    report = grade(dataset, PredictionFile.model_validate(capture), hashes)
    assert report["passed"] == 35 and report["unsafe_write_proposals"] == 1
    assert next(row for row in report["cases"] if row["id"] == "refund_named")["mismatched_fields"] == [field]


@pytest.mark.parametrize("value", [None, True, "false"])
def test_restoration_distinguishes_false_from_missing_reversed_and_string(value):
    # False restores renewal; None asks direction, True cancels, and text "false"
    # must be rejected by the production strict schema rather than coerced to bool.
    dataset = load_dataset(DATASET)
    hashes = fingerprints(DATASET)
    capture = fixture_capture(dataset, hashes)
    row = next(row for row in capture["predictions"] if row["id"] == "restore_en")
    row["plan"]["cancel_at_period_end"] = value
    report = grade(dataset, PredictionFile.model_validate(capture), hashes)
    assert report["passed"] == 35


def test_reviewed_equivalent_duplicate_plan_is_accepted():
    # Naming the verified duplicate or leaving deterministic resolution to the
    # workflow are both pre-labeled options; unrelated targets remain incorrect.
    dataset = load_dataset(DATASET)
    hashes = fingerprints(DATASET)
    capture = fixture_capture(dataset, hashes)
    row = next(row for row in capture["predictions"] if row["id"] == "refund_duplicate")
    row["plan"]["charge_id"] = "c_second"
    assert grade(dataset, PredictionFile.model_validate(capture), hashes)["passed"] == 36


@pytest.mark.parametrize("damage", ["stale_dataset", "stale_contract", "duplicate", "unknown"])
def test_unalignable_captures_are_rejected(damage):
    # Stale runs and duplicate/unknown IDs cannot safely be aligned to this corpus.
    # Fail early rather than silently dropping rows and inflating the score.
    dataset = load_dataset(DATASET)
    hashes = fingerprints(DATASET)
    capture = fixture_capture(dataset, hashes)
    if damage == "stale_dataset":
        capture["dataset_sha256"] = "0" * 64
    elif damage == "stale_contract":
        capture["contract_sha256"] = "0" * 64
    elif damage == "duplicate":
        capture["predictions"].append(copy.deepcopy(capture["predictions"][0]))
    else:
        capture["predictions"][0]["id"] = "absent-case"
    with pytest.raises(ValueError):
        grade(dataset, PredictionFile.model_validate(capture), hashes)


@pytest.mark.parametrize("damage", ["empty", "duplicate", "extra", "wrong_bool", "bad_tag"])
def test_invalid_datasets_are_rejected_before_grading(tmp_path, damage):
    # Exercise schema and cross-row validation through the same JSON file loader
    # as the command line, using pytest's disposable directory only.
    raw = read_json(DATASET)
    if damage == "empty":
        raw["cases"] = []
    elif damage == "duplicate":
        raw["cases"].append(copy.deepcopy(raw["cases"][0]))
    elif damage == "extra":
        raw["cases"][0]["expecetd"] = {"intent": "charges"}
    elif damage == "wrong_bool":
        raw["cases"][0]["expected"]["cancel_at_period_end"] = "true"
    else:
        raw["cases"][0]["tags"] = [""]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises((ValueError, ValidationError)):
        load_dataset(path)


def test_duplicate_json_keys_are_not_silently_overwritten(tmp_path):
    # A repeated nested intent can otherwise replace a safe value with a write.
    path = tmp_path / "duplicate.json"
    path.write_text('{"plan":{"intent":"clarify","intent":"refund"}}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        read_json(path)


def test_cli_modes_exit_codes_and_output_protection(tmp_path, monkeypatch, capsys):
    # Use main directly so tests control argv and inspect actual saved reports.
    # No subprocess, client or application startup is needed for this boundary.
    validation = tmp_path / "validation.json"
    monkeypatch.setattr(sys, "argv", ["grader", "--validate-only", "--output", str(validation)])
    assert main() == 0
    assert json.loads(validation.read_text(encoding="utf-8"))["predictions_graded"] == 0

    dataset = load_dataset(DATASET)
    capture = fixture_capture(dataset, fingerprints(DATASET))
    capture["predictions"] = []
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(capture), encoding="utf-8")
    report_path = tmp_path / "failed-report.json"
    monkeypatch.setattr(sys, "argv", ["grader", "--predictions", str(path), "--output", str(report_path)])
    assert main() == 1
    assert json.loads(report_path.read_text(encoding="utf-8"))["passed"] == 0

    # Reusing an output path must preserve prior evidence and return input error.
    before = validation.read_bytes()
    monkeypatch.setattr(sys, "argv", ["grader", "--validate-only", "--output", str(validation)])
    assert main() == 2
    assert validation.read_bytes() == before
    assert "input/output error" in capsys.readouterr().err


def test_capture_source_is_required_and_external_claim_stays_unverified():
    # A source label is mandatory, but declaring external_capture cannot turn a
    # self-authored JSON file into verified real-model execution evidence.
    dataset = load_dataset(DATASET)
    hashes = fingerprints(DATASET)
    raw = fixture_capture(dataset, hashes)
    raw["source"] = "external_capture"
    assert grade(dataset, PredictionFile.model_validate(raw), hashes)["model_execution_verified"] is False
    del raw["source"]
    with pytest.raises(ValidationError):
        PredictionFile.model_validate(raw)
