import json
import logging

import pytest
from anthropic import APIStatusError
from opentelemetry.trace import StatusCode
from prometheus_client import REGISTRY

from observability.telemetry import JsonFormatter, estimate_cost_usd, llm_component, tracer
from tests.conftest import TEST_MODEL


def _sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_cost_uses_longest_matching_prefix():
    # claude-3-5-haiku: $0.8 / $4 per 1M tokens
    assert estimate_cost_usd("claude-3-5-haiku-20241022", 1_000_000, 0) == pytest.approx(0.8)
    assert estimate_cost_usd("claude-3-5-haiku-20241022", 0, 1_000_000) == pytest.approx(4.0)


def test_unknown_model_cost_is_zero():
    assert estimate_cost_usd("some-unknown-model", 1000, 1000) == 0.0


async def test_llm_call_emits_genai_span_and_metrics(fake_llm, spans):
    labels = {"component": "unit.test", "model": TEST_MODEL}
    before_calls = _sample("companyagent_llm_requests_total", status="ok", **labels)
    before_in = _sample("companyagent_llm_tokens_total", direction="input", **labels)

    with llm_component("unit.test"):
        await fake_llm.client().messages.create(
            model=TEST_MODEL, max_tokens=64, messages=[{"role": "user", "content": "hi"}],
        )

    (span,) = [s for s in spans.get_finished_spans() if s.name == f"chat {TEST_MODEL}"]
    assert span.attributes["gen_ai.system"] == "anthropic"
    assert span.attributes["gen_ai.request.model"] == TEST_MODEL
    assert span.attributes["gen_ai.request.max_tokens"] == 64
    assert span.attributes["gen_ai.usage.input_tokens"] == 100
    assert span.attributes["gen_ai.usage.output_tokens"] == 20
    assert span.attributes["companyagent.llm.component"] == "unit.test"
    assert "gen_ai.prompt" not in span.attributes  # 默认不记录对话内容（隐私）

    assert _sample("companyagent_llm_requests_total", status="ok", **labels) == before_calls + 1
    assert _sample("companyagent_llm_tokens_total", direction="input", **labels) == before_in + 100


async def test_llm_error_marks_span_and_counts_error(fake_llm, spans):
    fake_llm.failing_agents.add("billing")
    labels = {"component": "unit.error", "model": TEST_MODEL, "status": "error"}
    before = _sample("companyagent_llm_requests_total", **labels)

    with llm_component("unit.error"), pytest.raises(APIStatusError):
        await fake_llm.client().messages.create(
            model=TEST_MODEL, max_tokens=64, system="You are a billing-support specialist",
            messages=[{"role": "user", "content": "refund"}],
        )

    (span,) = [s for s in spans.get_finished_spans() if s.name.startswith("chat ")]
    assert span.status.status_code == StatusCode.ERROR
    assert _sample("companyagent_llm_requests_total", **labels) == before + 1


def test_json_log_contains_trace_id():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    with tracer.start_as_current_span("log-test") as span:
        line = json.loads(JsonFormatter().format(record))
    assert line["msg"] == "hello world"
    assert line["trace_id"] == format(span.get_span_context().trace_id, "032x")
