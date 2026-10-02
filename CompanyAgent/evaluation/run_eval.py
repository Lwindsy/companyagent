"""
亮点：CI 评测卡点（Eval Gate）

核心问题：改了 prompt / 模型 / skills 之后，怎么知道 Agent 没有变差？

做法：
  1. 固定评测集（evaluation/datasets/*.jsonl，随代码一起做版本管理）
  2. 跑意图识别准确率 + LLM-as-Judge 对话质量（复用 EndToEndEvaluator）
  3. 两道卡：
       - 绝对下限：thresholds.json 的 min_scores，低于即失败
       - 相对退化：与仓库里提交的 baseline.json 比，任一指标退化超过 max_regression 即失败
     另外 Judge 自身失败太多（例如 API 限流）时判定本次评测无效，而不是"假通过"。
  4. 输出 JSON + Markdown 报告；在 GitHub Actions 里自动写入 Job Summary。
  5. 同时统计这次评测花了多少 token / 美元（来自 observability 的 Prometheus 计数器）。

评测范围：Agent 核心链路（意图识别 → 路由 → Agent 回答），不依赖 Redis / ChromaDB，
所以在 CI 里只需要一个 LLM API Key。

用法：
  python -m evaluation.run_eval                    # 跑评测并卡点，失败时退出码 1
  python -m evaluation.run_eval --update-baseline  # 当前结果写为新基线（需人工确认后提交）
"""
import argparse
import asyncio
import json
import os
import pathlib
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from prometheus_client import REGISTRY  # noqa: E402

from evaluation.evaluator import EndToEndEvaluator, EvalReport, IntentTestCase  # noqa: E402
from observability.telemetry import (  # noqa: E402
    configure_logging, instrument_anthropic, setup_tracing, shutdown_tracing, tracer,
)

EVAL_DIR = ROOT / "evaluation"
QUALITY_METRICS = ("relevance", "accuracy", "completeness", "helpfulness")


def load_jsonl(path: pathlib.Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def llm_usage() -> Dict[str, float]:
    """从 Prometheus 计数器汇总本进程内所有 LLM 调用的次数、token 和成本。"""
    usage = {"calls": 0.0, "input_tokens": 0.0, "output_tokens": 0.0, "cost_usd": 0.0}
    for metric in REGISTRY.collect():
        for s in metric.samples:
            if s.name == "companyagent_llm_requests_total":
                usage["calls"] += s.value
            elif s.name == "companyagent_llm_tokens_total":
                usage[f"{s.labels['direction']}_tokens"] += s.value
            elif s.name == "companyagent_llm_cost_usd_total":
                usage["cost_usd"] += s.value
    return usage


def judge_failure_rate(report: EvalReport) -> float:
    dialog = [r for r in report.results if r.test_id.startswith("dialog_")]
    if not dialog:
        return 0.0
    return sum(1 for r in dialog if r.metadata.get("judge_failed")) / len(dialog)


def check_gate(
    scores: Dict[str, float],
    thresholds: Dict[str, Any],
    baseline: Optional[Dict[str, float]],
    judge_failures: float,
) -> List[str]:
    """返回所有未通过的检查项；空列表表示通过。"""
    failures: List[str] = []

    max_judge_fail = thresholds.get("max_judge_failure_rate", 0.2)
    if judge_failures > max_judge_fail:
        failures.append(f"judge failure rate {judge_failures:.0%} > {max_judge_fail:.0%} (evaluation is not trustworthy)")

    for metric, minimum in thresholds.get("min_scores", {}).items():
        if metric not in scores:
            failures.append(f"{metric}: missing from results")
        elif scores[metric] < minimum:
            failures.append(f"{metric}: {scores[metric]:.3f} < minimum {minimum:.2f}")

    max_reg = thresholds.get("max_regression", 0.05)
    for metric, base in (baseline or {}).items():
        if metric in scores and base > 0:
            drop = (base - scores[metric]) / base
            if drop > max_reg:
                failures.append(f"{metric}: {base:.3f} -> {scores[metric]:.3f} regressed {drop:.1%} (> {max_reg:.0%})")
    return failures


def render_markdown(
    report: EvalReport,
    failures: List[str],
    baseline: Optional[Dict[str, float]],
    thresholds: Dict[str, Any],
    usage: Dict[str, float],
    model: str,
    seconds: float,
) -> str:
    status = "❌ FAILED" if failures else "✅ PASSED"
    lines = [
        f"## CompanyAgent eval gate: {status}",
        "",
        f"Model `{model}` · {report.total} checks · {seconds:.0f}s · "
        f"{int(usage['calls'])} LLM calls · {int(usage['input_tokens'] + usage['output_tokens'])} tokens · "
        f"${usage['cost_usd']:.4f}",
        "",
        "| Metric | Score | Minimum | Baseline | Δ |",
        "|---|---|---|---|---|",
    ]
    mins = thresholds.get("min_scores", {})
    for metric, value in report.avg_scores.items():
        base = (baseline or {}).get(metric)
        delta = f"{value - base:+.3f}" if base is not None else "—"
        base_text = f"{base:.3f}" if base is not None else "—"
        min_text = f"{mins[metric]:.2f}" if metric in mins else "—"
        lines.append(f"| {metric} | {value:.3f} | {min_text} | {base_text} | {delta} |")
    if baseline is None:
        lines += ["", "_No baseline committed yet — regression check skipped. Run with `--update-baseline`._"]
    if failures:
        lines += ["", "### Failures", *[f"- {f}" for f in failures]]

    weak = [r for r in report.results if not r.passed]
    if weak:
        lines += ["", "<details><summary>Failing cases</summary>", ""]
        for r in weak:
            lines.append(f"- `{r.test_id}` {r.detail}")
            if r.test_id == "intent_recognition":
                for c in r.metadata.get("cases", []):
                    if c["expected"] != c["predicted"]:
                        lines.append(f"  - \"{c['message']}\": expected `{c['expected']}`, got `{c['predicted']}`")
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


async def evaluate(
    evaluator: EndToEndEvaluator,
    intent_cases: List[IntentTestCase],
    dialog_cases: List[Dict[str, Any]],
    thresholds: Dict[str, Any],
    baseline: Optional[Dict[str, float]],
) -> tuple[EvalReport, List[str]]:
    with tracer.start_as_current_span("eval.run") as span:
        report = await evaluator.run(intent_cases=intent_cases, dialog_cases=dialog_cases)
        failures = check_gate(report.avg_scores, thresholds, baseline, judge_failure_rate(report))
        span.set_attribute("companyagent.eval.passed", not failures)
        for metric, value in report.avg_scores.items():
            span.set_attribute(f"companyagent.eval.{metric}", value)
    return report, failures


def _build_evaluator(baseline_path: Optional[str] = None) -> tuple[EndToEndEvaluator, str]:
    from agents.agent_orchestrator import AgentOrchestrator
    from core.skill_loader import SkillManager

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip() or None
    model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022").strip()

    skills = SkillManager(
        root_dir=os.getenv("COMPANYAGENT_SKILLS_DIR", str(ROOT / "skills")),
        max_prompt_chars=int(os.getenv("COMPANYAGENT_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    skills.load()
    orchestrator = AgentOrchestrator(api_key=api_key, base_url=base_url, model=model, skill_manager=skills)
    evaluator = EndToEndEvaluator(
        orchestrator=orchestrator,
        recognizer=orchestrator._intent_recognizer,
        api_key=api_key,
        base_url=base_url,
        model=model,
        baseline_path=baseline_path,  # None：评测过程中不改写任何基线文件
    )
    return evaluator, model


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the CompanyAgent eval gate")
    parser.add_argument("--intent-cases", default=str(EVAL_DIR / "datasets" / "intent_cases.jsonl"))
    parser.add_argument("--dialog-cases", default=str(EVAL_DIR / "datasets" / "dialog_cases.jsonl"))
    parser.add_argument("--thresholds", default=str(EVAL_DIR / "thresholds.json"))
    parser.add_argument("--baseline", default=str(EVAL_DIR / "baseline.json"))
    parser.add_argument("--out-dir", default=str(ROOT / "eval-results"))
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    configure_logging()
    setup_tracing()
    instrument_anthropic()

    thresholds = json.loads(pathlib.Path(args.thresholds).read_text(encoding="utf-8"))
    baseline_file = pathlib.Path(args.baseline)
    baseline = json.loads(baseline_file.read_text(encoding="utf-8"))["avg_scores"] if baseline_file.exists() else None
    intent_cases = [IntentTestCase(**c) for c in load_jsonl(pathlib.Path(args.intent_cases))]
    dialog_cases = load_jsonl(pathlib.Path(args.dialog_cases))

    evaluator, model = _build_evaluator()
    t0 = time.monotonic()
    report, failures = asyncio.run(evaluate(evaluator, intent_cases, dialog_cases, thresholds, baseline))
    seconds = time.monotonic() - t0
    usage = llm_usage()
    shutdown_tracing()

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(report, failures, baseline, thresholds, usage, model, seconds)
    (out_dir / "report.md").write_text(markdown, encoding="utf-8")
    (out_dir / "report.json").write_text(json.dumps({
        "model": model, "passed": not failures, "failures": failures,
        "usage": usage, "seconds": round(seconds, 1), "report": asdict(report),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(markdown)
    print(markdown)

    if args.update_baseline:
        baseline_file.write_text(json.dumps({
            "model": model,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "avg_scores": report.avg_scores,
        }, indent=2) + "\n", encoding="utf-8")
        print(f"Baseline written to {baseline_file}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
