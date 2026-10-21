"""Offline outcome checks with scripted extraction. NOT a real-model accuracy benchmark."""
import argparse
import asyncio
import json
import pathlib
import sys
import tempfile

# Resolve imports relative to this file, independent of the terminal's current folder.
# This makes the evaluator usable both as a script and as an imported test helper.
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from business.db import Database  # noqa: E402
from business.service import BusinessService  # noqa: E402
from workflows.models import ExtractedPlan  # noqa: E402
from workflows.service import WorkflowService  # noqa: E402


class Step(BaseModel):
    # One customer turn supplies a scripted plan. Repeated request_id values exercise
    # retry behavior; optional decision/note fields schedule an offline staff decision.
    model_config = ConfigDict(extra="forbid")
    message: str
    plan: ExtractedPlan
    request_id: str | None = None
    expected_status: str | None = None
    decision: str | None = None
    note: str = ""


class Expected(BaseModel):
    # Counts describe final database facts, not how convincing the assistant sounds.
    # ge=0 rejects negative expectations instead of quietly accepting a broken dataset.
    model_config = ConfigDict(extra="forbid")
    refunds: int = Field(ge=0)
    actions: int = Field(ge=0)
    tickets: int = Field(default=0, ge=0)
    cancel_at_period_end: bool = False
    extraction_calls: int | None = None


class Scenario(BaseModel):
    # Validate a fictional persona, at least one step, and final expected outcomes.
    # Unknown fields are rejected so misspelled grading criteria are not ignored.
    model_config = ConfigDict(extra="forbid")
    id: str
    persona_id: str
    steps: list[Step] = Field(min_length=1)
    expected: Expected


class ScriptedExtractor:
    # This fake returns a supplied plan and counts calls without any model/network I/O.
    # A passing outcome therefore does not establish real-language extraction accuracy.
    def __init__(self):
        self.calls = 0
        self.plan = ExtractedPlan(intent="clarify")

    async def extract(self, *_):
        self.calls += 1
        return self.plan


async def run_scenario(scenario: Scenario) -> dict:
    # Return named checks using an isolated temporary database. Leaving the with block
    # cleans it up on success or failure; no existing business database is opened.
    checks = []
    try:
        with tempfile.TemporaryDirectory(prefix="companyagent-scenario-") as folder:
            db = Database(str(pathlib.Path(folder) / "business.db"))
            db.initialize()
            business = BusinessService(db, clock=lambda: 1_800_000_000)
            # Fix UTC epoch seconds so payment ages are reproducible across test dates.
            extractor = ScriptedExtractor()
            workflow = WorkflowService(business, extractor)
            created = workflow.sessions.create(scenario.persona_id, "offline-evaluator")
            session = workflow.sessions.authenticate(created["token"])
            for index, step in enumerate(scenario.steps):
                # Set the next synthetic plan before each customer turn. An explicit
                # repeated request ID tests replay rather than a second business intent.
                extractor.plan = step.plan
                response = await workflow.chat(session, step.message, session.conv_id, step.request_id or f"step-{index}")
                if step.expected_status:
                    # operation_id in the reply refers to the action UUID. Locate that
                    # action instead of assuming the first displayed row is the target.
                    action = next((a for a in response["actions"] if a["id"] == response["operation_id"]), None)
                    checks.append({"check": f"step-{index}-status", "passed": bool(action and action["status"] == step.expected_status)})
                if step.decision:
                    # Use the same local approval transaction as the staff HTTP adapter.
                    if not response["operation_id"]:
                        raise ValueError("Approval scheduled without an operation")
                    decided = business.decide(response["operation_id"], step.decision, "offline-supervisor", step.note)
                    checks.append({"check": f"step-{index}-decision", "passed": decided["status"] == ("executed" if step.decision == "approve" else "rejected")})
            with db.connect() as conn:
                for table in ("refunds", "actions", "tickets"):
                    # Interpolate ONLY this fixed table allowlist as SQL syntax.
                    # Scope values stay bound ? parameters, never concatenated input.
                    count = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE sandbox_id=? AND customer_id=?",
                                         (session.scope.sandbox_id, session.scope.customer_id)).fetchone()[0]
                    checks.append({"check": table, "actual": count, "expected": getattr(scenario.expected, table),
                                   "passed": count == getattr(scenario.expected, table)})
            renewal = business.overview(session.scope)["cancel_at_period_end"]
            checks.append({"check": "cancel_at_period_end", "actual": renewal, "passed": renewal == scenario.expected.cancel_at_period_end})
            if scenario.expected.extraction_calls is not None:
                checks.append({"check": "extraction_calls", "actual": extractor.calls,
                               "passed": extractor.calls == scenario.expected.extraction_calls})
    except Exception as error:
        # Preserve failed cases in the report instead of aborting the whole suite.
        # These verbose errors are for synthetic fixtures, not a place for real secrets.
        checks.append({"check": "scenario_execution", "passed": False, "error": f"{type(error).__name__}: {error}"})
    return {"id": scenario.id, "passed": bool(checks) and all(c["passed"] for c in checks), "checks": checks}


async def run_all(path: pathlib.Path) -> dict:
    # JSON cannot contain comments; F05 explains dataset fields. Validate cases and
    # unique IDs before running scenarios sequentially against independent databases.
    scenarios = [Scenario.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]
    if not scenarios or len({s.id for s in scenarios}) != len(scenarios):
        raise ValueError("Scenario IDs must be unique and nonempty")
    results = [await run_scenario(scenario) for scenario in scenarios]
    return {"kind": "scripted_workflow_outcomes", "uses_real_model": False,
            "limitations": "Validates fixed workflows with supplied intents. Does not measure language understanding, live-model safety, latency or cost.",
            "passed": sum(r["passed"] for r in results), "total": len(results), "scenarios": results}


def main() -> int:
    # argparse reads terminal options. Exit 1 on any failed case makes CI fail even
    # when a report file was successfully generated; exit 0 means all checks passed.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=pathlib.Path, default=ROOT / "evaluation/datasets/business_scenarios.json")
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    report = asyncio.run(run_all(args.scenarios))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        # Write only the requested report path, creating its parent if needed. The
        # checked-in baseline is not overwritten unless explicitly selected as output.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(f"Scripted business outcomes: {report['passed']}/{report['total']} passed; real model: no")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    # Importing exposes helpers without executing this block. Direct execution sends
    # main's numeric success/failure status back to the shell through SystemExit.
    raise SystemExit(main())
