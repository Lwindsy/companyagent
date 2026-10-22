"""Opt-in, budgeted capture of synthetic intent cases using the production extractor.

Importing this module never loads credentials or sends requests. The CLI requires
--live, explicit prices, an output path, and a shared dollar ledger. Only plans,
token counts, model names and timings are saved; keys, URLs and raw errors are not.
The offline grader remains separate so model output cannot change the answer key.
"""

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

# Direct script execution otherwise searches evaluation/ rather than CompanyAgent.
# This affects imports only and never selects a deployed database or an env file.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.grade_intent_predictions import fingerprints, load_dataset  # noqa: E402
from workflows.anthropic_extractor import AnthropicExtractor  # noqa: E402


class BudgetStop(Exception):
    """No further paid request may be sent under this run's remaining allowance."""


class DollarLedger:
    """Persist dollars already used OR conservatively held for uncertain requests.

    path is an explicitly selected local JSON file; limit is a positive USD Decimal.
    A process lock prevents two captures from spending the same balance. A killed
    process leaves both its reservation and lock intact, requiring manual review.
    This is an experiment guard, not a product-wide billing system or invoice.
    """

    def __init__(self, path: Path, limit: Decimal):
        self.path, self.limit = path, limit
        self.lock = path.with_suffix(path.suffix + ".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation fails instead of silently taking over another capture.
        self.lock.touch(exist_ok=False)
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            self.used = Decimal(data.get("used_or_reserved_usd", "0"))
            if data and Decimal(data["limit_usd"]) != limit:
                raise ValueError("A ledger cannot silently change its limit")
            if not self.used.is_finite() or not 0 <= self.used <= limit:
                raise ValueError("Invalid ledger balance")
        except BaseException:
            self.lock.unlink()
            raise

    def save(self):
        # Replace only this ledger through a neighboring temporary file. Writing the
        # reservation BEFORE inference keeps a crash from making spent money reusable.
        temporary = self.path.with_suffix(self.path.suffix + ".pending")
        temporary.write_text(json.dumps({"limit_usd": str(self.limit),
                                         "used_or_reserved_usd": str(self.used)}) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def reserve(self, amount: Decimal):
        # Equality is allowed; a negative/nonfinite reservation is never a refund.
        if not amount.is_finite() or amount <= 0 or self.used + amount > self.limit:
            raise BudgetStop()
        self.used += amount
        self.save()

    def settle(self, held: Decimal, measured: Decimal):
        # Release unused reservation only after a complete response with valid usage.
        # Unknown/time-out failures retain the entire hold and stop the capture.
        if not measured.is_finite() or not 0 <= measured <= held <= self.used:
            raise BudgetStop()
        self.used += measured - held
        self.save()

    def close(self):
        # Delete only this process's exact lock; never remove a directory or ledger.
        self.lock.unlink()


class MeteredMessages:
    """Wrap SDK messages.create while leaving the production extraction prompt intact.

    Prices are USD per million tokens, supplied from a dated official price table.
    The wrapper disables optional server-side fallback so a different model cannot
    silently exceed the selected price. It also records the full SDK usage before
    the extractor parses JSON, so malformed answers are still charged and graded.
    """

    def __init__(self, client, ledger, input_price, output_price):
        self.client, self.ledger = client, ledger
        self.input_price, self.output_price = input_price, output_price
        self.records = []

    async def create(self, **kwargs):
        # Copy nested settings instead of mutating another caller's dictionary.
        kwargs["extra_body"] = dict(kwargs.get("extra_body", {}))
        kwargs["extra_body"].pop("fallbacks", None)
        kwargs["extra_headers"] = dict(kwargs.get("extra_headers", {}))
        kwargs["extra_headers"].pop("anthropic-beta", None)
        # For this text-only corpus, twice the UTF-8 byte length plus 4096 tokens
        # deliberately over-reserves input framing. Output reserves max_tokens,
        # including thinking tokens. No tools, caching directives or fast mode are used.
        payload = {key: kwargs[key] for key in ("model", "system", "messages")}
        # Schema-constrained output adds a provider-side formatting prompt. Reserve
        # its serialized schema as well as the existing generous framing allowance.
        payload["output_config"] = kwargs["extra_body"].get("output_config", {})
        input_bound = 2 * len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 4096
        held = (input_bound * self.input_price + kwargs["max_tokens"] * self.output_price) / Decimal(1_000_000)
        self.ledger.reserve(held)
        record = {"reserved_usd": str(held), "completed": False}
        self.records.append(record)
        started = time.perf_counter()
        try:
            response = await self.client.messages.create(**kwargs)
            usage = response.usage
            incoming, outgoing = usage.input_tokens, usage.output_tokens
            cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            # Cache creation is conservatively priced at twice base input; reading
            # cache at full base price also overestimates discounted official rates.
            if any(type(value) is not int or value < 0 for value in (incoming, outgoing, cache_write, cache_read)):
                raise BudgetStop()
            measured = ((incoming + 2 * cache_write + cache_read) * self.input_price
                        + outgoing * self.output_price) / Decimal(1_000_000)
            record.update({"completed": True, "input_tokens": incoming, "output_tokens": outgoing,
                           "cache_creation_input_tokens": cache_write, "cache_read_input_tokens": cache_read,
                           "model": response.model, "stop_reason": response.stop_reason,
                           "estimated_usd": str(measured)})
            # Never release a hold if the server reports a different model family.
            if not response.model.startswith(kwargs["model"]):
                raise BudgetStop()
            self.ledger.settle(held, measured)
            return response
        finally:
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)


async def capture(client, dataset, model, meter, capture_path, evidence_path, hashes):
    """Capture each case once; persist partial evidence before propagating failures.

    The supplied client is already configured. Only message/recent/overview enter
    the extractor; expected answers, tags and rationales never enter its prompt.
    Stop on transport/budget failure rather than generating an expensive retry.
    Invalid model JSON is one failed answer and can continue to the next case.
    """
    extractor = AnthropicExtractor(SimpleNamespace(messages=meter), model)
    answers = []
    evidence = {"kind": "live_intent_capture", "started_utc": datetime.now(timezone.utc).isoformat(),
                "sdk_retries": 0, "server_fallback_disabled": True, "model": model,
                "input_usd_per_million": str(meter.input_price),
                "output_usd_per_million": str(meter.output_price),
                "pricing_source": "https://platform.claude.com/docs/en/about-claude/pricing",
                "price_checked_date": "2026-10-22", "calls": meter.records,
                "limitations": "One synthetic corpus pass; estimated token pricing is not an account invoice."}

    def persist():
        # These paths were exclusively claimed by the CLI, so updates cannot overwrite
        # an earlier experiment. No raw response text or request credentials are saved.
        capture_path.write_text(json.dumps({"source": "external_capture", "model": model, **hashes,
                                           "predictions": answers}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence["used_or_reserved_usd"] = str(meter.ledger.used)
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        for case in dataset.cases:
            try:
                plan = await extractor.extract(case.message, [turn.model_dump() for turn in case.recent], case.overview)
                answer = plan.model_dump()
            except (ValueError, TypeError) as error:
                # Authentication/configuration TypeError can happen before any HTTP
                # response. Continue only if the model response was fully accounted.
                if not meter.records or not meter.records[-1]["completed"]:
                    raise
                answer = None
                meter.records[-1]["parse_error"] = type(error).__name__
            answers.append({"id": case.id, "plan": answer})
            persist()
            print(f"Captured {len(answers)}/{len(dataset.cases)}: {case.id}; ledger USD {meter.ledger.used}", flush=True)
    finally:
        # On cancellation the held dollars stay in the ledger; partial captures remain
        # gradeable as failures/missing answers, without quietly rerunning paid cases.
        persist()


def main() -> int:
    """Parse explicit opt-in settings; exit 2 for configuration or capture failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly allow paid inference")
    parser.add_argument("--env-file", type=Path, help="Explicit authorized env file; never printed")
    parser.add_argument("--model", help="Defaults to the configured ANTHROPIC_MODEL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--budget-usd", type=Decimal, required=True)
    parser.add_argument("--input-usd-per-million", type=Decimal, required=True)
    parser.add_argument("--output-usd-per-million", type=Decimal, required=True)
    args = parser.parse_args()
    prices = (args.budget_usd, args.input_usd_per_million, args.output_usd_per_million)
    if not args.live or any(not value.is_finite() or value <= 0 for value in prices):
        parser.error("Explicit --live and positive finite prices/budget are required")
    if args.env_file:
        from dotenv import load_dotenv
        if not args.env_file.is_file():
            parser.error("The explicitly selected configuration file does not exist")
        load_dotenv(args.env_file, override=True)
    model = args.model or os.getenv("ANTHROPIC_MODEL", "").strip()
    if not model:
        parser.error("An explicit/configured model identifier is required")
    # Suppress SDK transport logging, which is unnecessary for a synthetic score and
    # could reveal private endpoint configuration. Errors below show classes only.
    for name in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    ledger = None
    try:
        dataset_path = ROOT / "evaluation/datasets/intent_cases.json"
        dataset = load_dataset(dataset_path)
        evidence_path = args.output.with_suffix(".evidence.json")
        if args.output.suffix != ".json" or args.output in (args.ledger, dataset_path):
            raise ValueError("Select a new capture JSON path")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Claim both outputs before constructing a client. An existing report blocks
        # inference; the script never overwrites it or guesses which run to resume.
        args.output.touch(exist_ok=False)
        evidence_path.touch(exist_ok=False)
        ledger = DollarLedger(args.ledger, args.budget_usd)

        async def run():
            from anthropic import AsyncAnthropic
            # SDK authentication uses only the authorized process environment. Check
            # the pricing scope without printing keys, endpoint paths or client objects.
            async with AsyncAnthropic(max_retries=0, timeout=20) as client:
                if client.base_url.host != "api.anthropic.com":
                    raise ValueError("This price guard requires the first-party endpoint")
                meter = MeteredMessages(client, ledger, args.input_usd_per_million, args.output_usd_per_million)
                await capture(client, dataset, model, meter, args.output, evidence_path, fingerprints(dataset_path))

        asyncio.run(run())
    except Exception as error:
        print(f"Capture stopped: {type(error).__name__}; inspect local evidence; no automatic retry", file=sys.stderr)
        return 2
    finally:
        if ledger is not None:
            ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
