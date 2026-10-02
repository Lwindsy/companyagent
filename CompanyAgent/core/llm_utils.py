"""LLM response helpers shared by Anthropic-compatible providers."""
import os
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

# 与 CompanyAgentDotnet 的 ClaudeModelCapabilities 保持一致。
# 这一代模型拒绝 temperature（400），且会先思考，思考 token 计入 max_tokens。
_CURRENT_GENERATION = ("claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-opus-4-8", "claude-opus-4-7")
_EFFORT_MODELS = _CURRENT_GENERATION + ("claude-opus-4-6", "claude-sonnet-4-6")
_SERVER_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_THINKING_MAX_TOKENS_FLOOR = 4096


def claude_request_kwargs(model: str, max_tokens: int, temperature: Optional[float] = None) -> Dict[str, Any]:
    """按模型代际生成 messages.create 的参数，避免把旧模型的参数发给新模型。

    DeepSeek 等兼容端点的模型名不匹配任何前缀，参数保持原样。
    """
    current = model.startswith(_CURRENT_GENERATION)
    kwargs: Dict[str, Any] = {
        "model": model,
        # 小预算（256）会被思考耗尽，导致 JSON 答案被截断，所以给一个下限
        "max_tokens": max(max_tokens, _THINKING_MAX_TOKENS_FLOOR) if current else max_tokens,
    }
    if temperature is not None and not current:
        kwargs["temperature"] = temperature

    extra_body: Dict[str, Any] = {}
    if model.startswith(_EFFORT_MODELS):
        effort = os.getenv("ANTHROPIC_EFFORT", "low").strip() or "low"
        extra_body["output_config"] = {"effort": effort}
    if model in _SERVER_FALLBACK_MODELS and _is_first_party_api():
        # 安全分类器拒答时，由服务端在同一次调用里换模型重答；代理/第三方网关可能不认这个 beta
        extra_body["fallbacks"] = "default"
        kwargs["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
    if extra_body:
        kwargs["extra_body"] = extra_body
    return kwargs


def _is_first_party_api() -> bool:
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    return not base_url or urlparse(base_url).hostname == "api.anthropic.com"


def extract_text_content(content: Iterable[Any]) -> str:
    """Return text blocks from Anthropic-style response content."""
    texts: List[str] = []
    for block in content or []:
        if isinstance(block, str):
            texts.append(block)
            continue

        block_type = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if isinstance(block, dict):
            block_type = block.get("type", block_type)
            text = block.get("text", text)

        if isinstance(text, str) and (block_type in (None, "text")):
            texts.append(text)

    return "\n".join(t for t in texts if t)
