"""
测试公共夹具。

- FakeLLM：用 httpx.MockTransport 冒充 Anthropic API。请求会走真实的 anthropic SDK
  （包括我们打的 tracing 补丁），只是不出网、不花钱、结果可控。
- spans：内存中的 span 导出器，用来断言 trace 结构。
"""
import json
import os
import pathlib
import sys

import httpx
import pytest
from anthropic import AsyncAnthropic
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

# 必须在业务模块 import 之前设置全局 TracerProvider；setup_tracing() 发现已存在会跳过。
_exporter = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_exporter))
trace.set_tracer_provider(_provider)

from observability.telemetry import instrument_anthropic  # noqa: E402

instrument_anthropic()

TEST_MODEL = "claude-3-5-haiku-test"


class FakeLLM:
    """按 prompt 内容返回预设回答的假 LLM。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.intent = "billing"
        self.intent_confidence = 0.9
        self.failing_agents: set[str] = set()   # 例如 {"billing"}：该 Agent 的调用返回 500
        self.judge_scores = {"relevance": 0.9, "accuracy": 0.9, "completeness": 0.8, "helpfulness": 0.85}

    def _reply(self, body: dict) -> tuple[int, str]:
        system = body.get("system") or ""
        prompt = str(body["messages"][-1]["content"])
        if "intent-classification expert" in prompt:
            return 200, json.dumps({"intent": self.intent, "confidence": self.intent_confidence, "reasoning": "test"})
        if "客服质量评估专家" in prompt:
            return 200, json.dumps(self.judge_scores)
        for agent in self.failing_agents:
            if f"{agent}-support specialist" in system:
                return 500, ""
        return 200, "Here are the steps to resolve your issue."

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        status, text = self._reply(body)
        if status != 200:
            return httpx.Response(status, json={"type": "error", "error": {"type": "api_error", "message": "boom"}})
        return httpx.Response(200, json={
            "id": f"msg_{len(self.calls)}",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        })

    def client(self) -> AsyncAnthropic:
        return AsyncAnthropic(
            api_key="test-key",
            base_url="http://llm.test",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
        )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def spans() -> InMemorySpanExporter:
    _exporter.clear()
    return _exporter


@pytest.fixture
def orchestrator(fake_llm):
    from agents.agent_orchestrator import AgentOrchestrator

    # base_url 非空会关闭 Embedding 分支，让意图识别结果只取决于假 LLM + 关键词，稳定可测。
    orch = AgentOrchestrator(api_key="test-key", base_url="http://llm.test", model=TEST_MODEL)
    client = fake_llm.client()
    orch._intent_recognizer.client = client
    for agents in orch._pool.values():
        for agent in agents:
            agent._client = client
    return orch
