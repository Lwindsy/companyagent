from evaluation.evaluator import EndToEndEvaluator, IntentTestCase
from evaluation.run_eval import check_gate, evaluate, render_markdown

THRESHOLDS = {
    "min_scores": {"intent_accuracy": 0.8, "relevance": 0.75},
    "max_regression": 0.05,
    "max_judge_failure_rate": 0.2,
}


def test_gate_passes_when_above_minimums_and_baseline():
    scores = {"intent_accuracy": 0.9, "relevance": 0.86}
    assert check_gate(scores, THRESHOLDS, {"intent_accuracy": 0.9, "relevance": 0.88}, 0.0) == []


def test_gate_fails_below_minimum():
    failures = check_gate({"intent_accuracy": 0.7, "relevance": 0.9}, THRESHOLDS, None, 0.0)
    assert len(failures) == 1 and failures[0].startswith("intent_accuracy")


def test_gate_fails_on_regression_even_above_minimum():
    # 0.95 -> 0.85 仍高于下限 0.75，但退化 10.5% > 5%
    failures = check_gate({"intent_accuracy": 0.9, "relevance": 0.85}, THRESHOLDS, {"relevance": 0.95}, 0.0)
    assert len(failures) == 1 and "regressed" in failures[0]


def test_gate_rejects_untrustworthy_judge():
    failures = check_gate({"intent_accuracy": 0.9, "relevance": 0.9}, THRESHOLDS, None, 0.5)
    assert "judge failure rate" in failures[0]


async def test_end_to_end_gate_with_fake_llm(orchestrator, fake_llm):
    evaluator = EndToEndEvaluator(
        orchestrator=orchestrator, recognizer=orchestrator._intent_recognizer, api_key="test-key",
    )
    evaluator._judge._client = fake_llm.client()
    fake_llm.intent = "refund"

    report, failures = await evaluate(
        evaluator,
        intent_cases=[IntentTestCase("I want my money back", "refund"), IntentTestCase("hello", "greeting")],
        dialog_cases=[{"question": "Refund please"}, {"turns": ["Hi", "Order #1 is late"]}],
        thresholds=THRESHOLDS,
        baseline=None,
    )
    # 假 LLM 永远回答 refund，所以意图准确率 50%，应被卡住
    assert report.avg_scores["intent_accuracy"] == 0.5
    assert report.avg_scores["relevance"] == 0.9
    assert failures == ["intent_accuracy: 0.500 < minimum 0.80"]

    usage = {"calls": 7, "input_tokens": 700, "output_tokens": 140, "cost_usd": 0.0}
    md = render_markdown(report, failures, None, THRESHOLDS, usage, "test-model", 1.0)
    assert "FAILED" in md and "expected `greeting`, got `refund`" in md
