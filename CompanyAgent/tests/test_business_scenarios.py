from pathlib import Path

import pytest

from evaluation.run_business_scenarios import Scenario, run_all, run_scenario


@pytest.mark.asyncio
async def test_fixed_scenario_suite_checks_database_outcomes():
    # Run the checked-in synthetic cases end-to-end through real local business logic.
    # The explicit uses_real_model=False prevents treating this as extraction accuracy.
    report = await run_all(Path(__file__).parents[1] / "evaluation/datasets/business_scenarios.json")
    assert report["uses_real_model"] is False
    assert report["total"] == 12 and report["passed"] == 12


@pytest.mark.asyncio
async def test_wrong_database_expectation_marks_report_failed():
    # A deliberately wrong expected refund count must fail the grader. This negative
    # control proves the report checks rows rather than blindly marking execution passed.
    scenario = Scenario.model_validate({"id": "deliberately-wrong", "persona_id": "p_b2b",
                                        "steps": [{"message": "Hello", "plan": {"intent": "clarify"}}],
                                        "expected": {"refunds": 1, "actions": 0}})
    result = await run_scenario(scenario)
    assert result["passed"] is False
    assert any(row["check"] == "refunds" and not row["passed"] for row in result["checks"])
