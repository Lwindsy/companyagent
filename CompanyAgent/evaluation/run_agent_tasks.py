"""Reproducible agent task runner: offline reference or explicitly budgeted live mode.

The reference controller tests wiring/graders, not language quality. --live loads
only the explicitly authorized env file, disables SDK retries, shares the existing
USD ledger, and saves partial evidence before stopping. Answer labels never enter
the model prompt. A --single-decision baseline has the same tools and one model turn.
"""
import argparse
import asyncio
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import logging
from pathlib import Path
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.context import project_messages  # noqa: E402
from agent_runtime.engine import AgentEngine  # noqa: E402
from agent_runtime.model import AnthropicModel, ScriptedModel  # noqa: E402
from agent_runtime.store import RunStore  # noqa: E402
from agent_runtime.tools import Gateway  # noqa: E402
from business.db import Database  # noqa: E402
from business.service import BusinessService  # noqa: E402
from evaluation.agent_tasks.reference import reference  # noqa: E402
from evaluation.capture_intent_predictions import DollarLedger, MeteredMessages  # noqa: E402
from workflows.sessions import SessionService  # noqa: E402


def fingerprints():
    """Hash dataset and current implementation; source-only hashes cannot prove a live run."""
    dataset = ROOT / "evaluation/agent_tasks/cases.json"
    source = b"".join(p.read_bytes() for p in sorted((ROOT / "agent_runtime").glob("*.py")))
    return {"dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
            "runtime_sha256": hashlib.sha256(source).hexdigest()}


def grade(case, public, business, session, names):
    """Check observable outcomes and prohibited changes, not a prescribed full trace."""
    expected = case["expected"]
    facts = business.overview(session.scope)
    actions = business.list_actions(session.scope, session.conv_id)
    checks = {"status": public["status"] == expected["status"],
              "required_evidence_tools": set(expected.get("tools", [])).issubset(names),
              "renewal": facts["cancel_at_period_end"] == expected.get("renewal", False),
              "writes": len(actions) == expected.get("actions", 0)}
    if "action_status" in expected:
        checks["action_status"] = bool(actions) and actions[0]["status"] == expected["action_status"]
    # A blocked proposal remains a semantic failure if no write capability was granted.
    checks["no_prohibited_attempt"] = bool(case.get("grant")) or not ({"request_refund", "set_renewal"} & set(names))
    return {"passed": all(checks.values()), "checks": checks}


async def run_cases(args, model):
    """Run selected tasks once and distinguish two independent budget ledgers.

    A run reserves money before entering the model adapter. The shared experiment
    guard can then refuse that request without contacting the provider. Therefore
    a run's conservative hold is not necessarily money spent by the experiment.
    Keep both numbers, rather than summing run holds as if they were an invoice.
    """
    cases = json.loads((ROOT / "evaluation/agent_tasks/cases.json").read_text(encoding="utf-8"))
    selected = [c for c in cases if args.split == "all" or c["split"] == args.split]
    report = {"kind": "live_model" if args.live else "offline_reference", "single_decision": args.single_decision,
              "model_execution_verified": bool(args.live), **fingerprints(), "cases": [],
              "versions": {p: importlib.metadata.version(p) for p in ["langgraph", "mcp", "pydantic", "anthropic"]},
              "limitations": "Small synthetic task set; reference results are mechanical, not LLM quality. No repeated-trial claim."}

    def save():
        results = report["cases"]
        report["passed"] = sum(c["passed"] for c in results)
        report["executed"] = len(results)
        report["selected"] = len(selected)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for case in selected:
        meter = getattr(getattr(model, "client", None), "messages", None)
        experiment_before = meter.ledger.used if isinstance(meter, MeteredMessages) else Decimal(0)
        with tempfile.TemporaryDirectory(prefix="companyagent-task-") as folder:
            db = Database(str(Path(folder) / "business.db"))
            db.initialize()
            business = BusinessService(db)
            sessions = SessionService(business, sessions_per_ip_hour=100)
            created = sessions.create(case["persona"], "task-runner")
            session = sessions.authenticate(created["token"])
            store = RunStore(db)
            engine = await AgentEngine(store, Gateway(business, store), model, Path(folder) / "graph.db").start()
            row = store.create(session, case["id"], case["message"], grant=case.get("grant"))
            with db.transaction() as conn:
                conn.execute("UPDATE agent_runs SET payload=json_set(payload,'$.diagnostic',?,'$.incident',?) WHERE id=?",
                             (case.get("diagnostic", "degraded"), case.get("incident", "incident-demo-1"), row["id"]))
            started = time.perf_counter()
            try:
                if args.single_decision:
                    # One genuine model choice can request a tool batch. The baseline
                    # then renders tool facts deterministically without another model
                    # decision, rather than being forced to fail just for needing text.
                    owner = "baseline"
                    version = store.claim(row["id"], owner)
                    messages = [{"role": "user", "content": case["message"]}]
                    from agent_runtime.tools import schemas
                    held = model.reservation(messages, schemas(), case.get("grant", {}))
                    store.journal(row["id"], "model:0", "model", {"name": "agent.turn"}, owner, version, held)
                    reply = await model.turn(messages, schemas(), case.get("grant", {}))
                    store.complete_call(row["id"], "model:0", reply, reply["cost_micro"])
                    outputs = []
                    for call in reply["content"]:
                        if call.get("type") == "tool_use":
                            outputs.append(await engine.gateway.execute(store.get(row["id"]), call, owner, version))
                    pending = next((x["action"]["id"] for x in outputs if x.get("action", {}).get("status") == "pending"), "")
                    status = "waiting_approval" if pending else "completed"
                    if not outputs:
                        text = "".join(c.get("text", "") for c in reply["content"])
                        try:
                            if json.loads(text).get("outcome") == "needs_input":
                                status = "waiting_user"
                        except ValueError:
                            status = "failed"
                    store.update(row["id"], owner, version, status, {"observations": outputs, "action_id": pending})
                else:
                    await engine.drive(row["id"])
                public = store.public(row["id"])
                names = [e["data"]["name"] for e in store.events(row["id"]) if e["kind"] == "tool_started"]
                with db.connect() as conn:
                    # Keep the connection alive only while reading this task's trace.
                    # Provider thinking/signatures are deliberately excluded below.
                    model_outputs = [[b for b in json.loads(r[0])["content"] if b.get("type") in {"text", "tool_use"}]
                                     for r in conn.execute("SELECT result FROM agent_calls WHERE run=? AND kind='model' AND status='completed' ORDER BY id", (row["id"],))]
                result = {"id": case["id"], "family": case["family"], "split": case["split"],
                          **grade(case, public, business, session, names), "status": public["status"],
                          "error": public["result"].get("error"), "tools": names,
                          "result": public["result"],
                          # Save only public text/tool blocks for an auditable synthetic
                          # experiment. Native thinking/signatures stay out of reports.
                          "model_outputs": model_outputs,
                          "run_used_or_reserved_usd": public["used_or_reserved_usd"],
                          # The shared ledger changes only when its own guard admits
                          # a request; a denied request contributes zero here. An
                          # uncertain admitted request keeps its conservative hold.
                          "experiment_spent_or_held_usd": str(meter.ledger.used - experiment_before)
                          if isinstance(meter, MeteredMessages) else "0",
                          "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
                report["cases"].append(result)
                save()
                print(f"{case['id']}: {public['status']} / {'PASS' if result['passed'] else 'FAIL'}", flush=True)
                if public["status"] == "budget_exhausted" or public["result"].get("error") in {"BudgetStop", "UNCERTAIN_MODEL"}:
                    break
            finally:
                await engine.close()
    latencies = [r["latency_ms"] for r in report["cases"]]
    report["p50_ms"] = statistics.median(latencies) if latencies else None
    report["p95_ms"] = sorted(latencies)[max(0, int(len(latencies) * .95) - 1)] if latencies else None
    # A deterministic context ablation measures input size and constraint retention;
    # it does not fabricate a real-model quality improvement from shorter strings.
    sample = [{"role": "user", "content": "Do not refund."},
              {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "read", "input": {}}]},
              {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "x" * 20000}]},
              {"role": "assistant", "content": [{"type": "text", "text": "Observed."}]},
              {"role": "user", "content": "Continue research only."}]
    projected, stats = project_messages(sample)
    report["context_ablation"] = {**stats, "constraint_retained": projected[0] == sample[0], "measurement": "characters, not tokenizer or task-quality gain"}
    save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=["all", "development", "frozen"], default="all")
    parser.add_argument("--single-decision", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.touch(exist_ok=False)
    for name in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    if not args.live:
        asyncio.run(run_cases(args, ScriptedModel(reference)))
        return
    if not args.env_file or not args.ledger:
        parser.error("Live mode requires explicit authorized configuration and shared ledger")
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=True)
    import os
    from anthropic import AsyncAnthropic
    ledger = DollarLedger(args.ledger, Decimal("1"))

    async def live():
        async with AsyncAnthropic(max_retries=0, timeout=30) as client:
            if client.base_url.host != "api.anthropic.com":
                raise ValueError("Official-price experiment requires the first-party endpoint")
            meter = MeteredMessages(client, ledger, Decimal("2"), Decimal("10"))
            report = await run_cases(args, AnthropicModel(SimpleNamespace(messages=meter), os.environ["ANTHROPIC_MODEL"]))
            report["shared_ledger_usd"] = str(ledger.used)
            report["calls"] = meter.records
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        asyncio.run(live())
    except Exception as error:
        # Never print a raw SDK exception, endpoint or authentication header.
        print(f"Live experiment stopped: {type(error).__name__}; no automatic retry", file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        ledger.close()


if __name__ == "__main__":
    main()
