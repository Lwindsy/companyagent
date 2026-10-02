"""按模型代际生成请求参数：新模型不能带 temperature，响应里的 thinking 块不能混进正文。"""
import json

import httpx
import pytest
from anthropic import AsyncAnthropic

from core.llm_utils import claude_request_kwargs, extract_text_content


def test_legacy_model_keeps_temperature_and_budget():
    kwargs = claude_request_kwargs("claude-haiku-4-5", max_tokens=256, temperature=0.1)
    assert kwargs == {"model": "claude-haiku-4-5", "max_tokens": 256, "temperature": 0.1}


def test_deepseek_model_is_untouched(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
    kwargs = claude_request_kwargs("deepseek-v4-pro", max_tokens=256, temperature=0.0)
    assert kwargs == {"model": "deepseek-v4-pro", "max_tokens": 256, "temperature": 0.0}


def test_sonnet_5_5_drops_temperature_and_adds_effort_and_fallback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_EFFORT", raising=False)
    kwargs = claude_request_kwargs("claude-sonnet-5-5", max_tokens=256, temperature=0.1)
    assert "temperature" not in kwargs
    assert kwargs["max_tokens"] == 4096
    assert kwargs["extra_body"] == {"output_config": {"effort": "low"}, "fallbacks": "default"}
    assert kwargs["extra_headers"] == {"anthropic-beta": "server-side-fallback-2026-07-01"}


def test_fallback_only_sent_to_first_party_api(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com")
    monkeypatch.setenv("ANTHROPIC_EFFORT", "medium")
    kwargs = claude_request_kwargs("claude-sonnet-5-5", max_tokens=1024)
    assert kwargs["extra_body"] == {"output_config": {"effort": "medium"}}
    assert "extra_headers" not in kwargs


@pytest.mark.asyncio
async def test_request_body_and_thinking_response_through_sdk(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "text", "text": '{"intent": "billing"}'},
            ],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

    client = AsyncAnthropic(api_key="test-key", max_retries=0,
                            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    resp = await client.messages.create(
        **claude_request_kwargs("claude-sonnet-5-5", max_tokens=256, temperature=0.0),
        messages=[{"role": "user", "content": "hi"}],
    )

    assert "temperature" not in seen["body"]
    assert seen["body"]["output_config"] == {"effort": "low"}
    assert seen["body"]["fallbacks"] == "default"
    assert seen["beta"] == "server-side-fallback-2026-07-01"
    assert extract_text_content(resp.content) == '{"intent": "billing"}'
