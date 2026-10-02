"""
亮点：Agent 系统的可观测性（Tracing + Metrics + 结构化日志）

核心问题：一次 /chat 请求内部有多次 LLM 调用（意图识别、查询改写、重排、
一个或多个 Agent、画像更新），出问题时如何定位是哪一步慢、哪一步错、花了多少 token？

本模块的答案：
  1. Tracing —— OpenTelemetry。每个请求一棵 span 树：
       POST /chat
         ├─ memory.get_context
         ├─ intent.recognize ── chat <model>   (LLM span)
         ├─ rag.retrieve ── chat <model> ×2    (改写 + 重排)
         └─ orchestrator.run
              └─ agent.billing ── chat <model>
     LLM span 遵循 OTel GenAI 语义约定（gen_ai.*），任何兼容后端都能识别：
     Jaeger、Grafana Tempo、Langfuse、Azure Monitor 等，只需改 OTLP endpoint。
  2. Metrics —— Prometheus。按 component（哪个环节）× model 统计
     LLM 调用次数、token、估算成本、延迟分布；外加 HTTP 和 /chat 业务指标。
  3. 日志 —— LOG_FORMAT=json 时输出 JSON 行，并带上 trace_id，
     日志和 trace 可以互相跳转。

LLM 埋点的实现方式：在启动时给 anthropic SDK 的 AsyncMessages.create 包一层，
所以所有调用点（Agent、意图识别、RAG、记忆、评测）不需要各自写埋点，
调用方只需用 llm_component("xxx") 标明"我是哪个环节"。
"""
import contextvars
import functools
import json
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode
from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "companyagent-api")
tracer = trace.get_tracer("companyagent")

# ── Prometheus 指标 ───────────────────────────────────────────────────────────
# 标签只用低基数字段（component / model / status），不要放 user_id、conv_id。

LLM_REQUESTS = Counter(
    "companyagent_llm_requests_total", "LLM calls", ["component", "model", "status"],
)
LLM_TOKENS = Counter(
    "companyagent_llm_tokens_total", "LLM tokens consumed", ["component", "model", "direction"],
)
LLM_COST = Counter(
    "companyagent_llm_cost_usd_total", "Estimated LLM cost in USD", ["component", "model"],
)
LLM_LATENCY = Histogram(
    "companyagent_llm_latency_seconds", "LLM call latency", ["component", "model"],
    buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64),
)
CHAT_REQUESTS = Counter(
    "companyagent_chat_requests_total", "Completed /chat requests",
    ["agent", "intent_group", "escalated", "knowledge_used"],
)
CHAT_LATENCY = Histogram(
    "companyagent_chat_latency_seconds", "End-to-end /chat latency", ["agent"],
    buckets=(0.5, 1, 2, 4, 8, 16, 32, 64),
)
HTTP_REQUESTS = Counter(
    "companyagent_http_requests_total", "HTTP requests", ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "companyagent_http_request_duration_seconds", "HTTP request latency", ["method", "route"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)


# ── 当前 LLM 调用属于哪个环节 ─────────────────────────────────────────────────
# contextvar 会被 asyncio.create_task / gather 自动复制，所以并行 Agent 互不干扰。

_component: contextvars.ContextVar[str] = contextvars.ContextVar("llm_component", default="unknown")


@contextmanager
def llm_component(name: str) -> Iterator[None]:
    """标记代码块内发出的 LLM 调用属于哪个环节，例如 "agent.billing"、"rag.rerank"。"""
    token = _component.set(name)
    try:
        yield
    finally:
        _component.reset(token)


# ── 成本估算 ──────────────────────────────────────────────────────────────────
# 单位：美元 / 百万 token。模型名按前缀匹配。价格会变，可用 LLM_PRICING_JSON 覆盖，
# 例如 {"deepseek-v4-pro": [0.5, 2.0]}。未知模型记 0，不瞎猜。

_DEFAULT_PRICING: Dict[str, tuple] = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-5-haiku": (0.8, 4.0),
}


def _load_pricing() -> Dict[str, tuple]:
    pricing = dict(_DEFAULT_PRICING)
    raw = os.getenv("LLM_PRICING_JSON", "").strip()
    if raw:
        try:
            pricing.update({k: tuple(v) for k, v in json.loads(raw).items()})
        except (ValueError, TypeError) as ex:
            logger.warning(f"Ignoring invalid LLM_PRICING_JSON: {ex}")
    return pricing


_PRICING = _load_pricing()


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    for prefix, (in_price, out_price) in sorted(_PRICING.items(), key=lambda kv: -len(kv[0])):
        if model.startswith(prefix):
            return (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    return 0.0


# ── Anthropic SDK 埋点 ────────────────────────────────────────────────────────

_CAPTURE_CONTENT = os.getenv("OTEL_CAPTURE_LLM_CONTENT", "false").lower() == "true"
_CONTENT_LIMIT = 2000


def _record_llm_call(component: str, model: str, status: str, seconds: float,
                     input_tokens: int = 0, output_tokens: int = 0) -> None:
    LLM_REQUESTS.labels(component, model, status).inc()
    LLM_LATENCY.labels(component, model).observe(seconds)
    if input_tokens or output_tokens:
        LLM_TOKENS.labels(component, model, "input").inc(input_tokens)
        LLM_TOKENS.labels(component, model, "output").inc(output_tokens)
        LLM_COST.labels(component, model).inc(estimate_cost_usd(model, input_tokens, output_tokens))


def _wrap_create(original):
    @functools.wraps(original)
    async def traced_create(self, *args: Any, **kwargs: Any):
        # 流式响应拿不到最终 usage，直接透传（本项目没有用到流式）。
        if kwargs.get("stream"):
            return await original(self, *args, **kwargs)

        component = _component.get()
        model = str(kwargs.get("model", "unknown"))
        with tracer.start_as_current_span(f"chat {model}", kind=SpanKind.CLIENT) as span:
            span.set_attribute("gen_ai.system", "anthropic")
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", model)
            span.set_attribute("companyagent.llm.component", component)
            for key in ("max_tokens", "temperature"):
                if key in kwargs:
                    span.set_attribute(f"gen_ai.request.{key}", kwargs[key])
            if _CAPTURE_CONTENT:
                span.set_attribute("gen_ai.prompt", json.dumps(kwargs.get("messages"), ensure_ascii=False)[:_CONTENT_LIMIT])

            t0 = time.perf_counter()
            try:
                resp = await original(self, *args, **kwargs)
            except Exception as ex:
                span.record_exception(ex)
                span.set_status(Status(StatusCode.ERROR, type(ex).__name__))
                span.set_attribute("error.type", type(ex).__name__)
                _record_llm_call(component, model, "error", time.perf_counter() - t0)
                raise

            usage = getattr(resp, "usage", None)
            in_tok = int(getattr(usage, "input_tokens", 0) or 0)
            out_tok = int(getattr(usage, "output_tokens", 0) or 0)
            span.set_attribute("gen_ai.usage.input_tokens", in_tok)
            span.set_attribute("gen_ai.usage.output_tokens", out_tok)
            span.set_attribute("gen_ai.response.model", str(getattr(resp, "model", model)))
            stop_reason = getattr(resp, "stop_reason", None)
            if stop_reason:
                span.set_attribute("gen_ai.response.finish_reasons", [str(stop_reason)])
            if _CAPTURE_CONTENT:
                from core.llm_utils import extract_text_content
                span.set_attribute("gen_ai.completion", extract_text_content(getattr(resp, "content", []))[:_CONTENT_LIMIT])

            _record_llm_call(component, model, "ok", time.perf_counter() - t0, in_tok, out_tok)
            return resp

    traced_create._companyagent_traced = True  # type: ignore[attr-defined]
    return traced_create


def instrument_anthropic() -> None:
    """给 AsyncMessages.create 加上 tracing + metrics。重复调用是安全的。"""
    from anthropic.resources.messages import AsyncMessages

    if getattr(AsyncMessages.create, "_companyagent_traced", False):
        return
    AsyncMessages.create = _wrap_create(AsyncMessages.create)  # type: ignore[method-assign]


# ── Tracer 初始化 ─────────────────────────────────────────────────────────────

def setup_tracing() -> None:
    """
    初始化 OpenTelemetry。导出目标完全由标准环境变量决定：
      OTEL_EXPORTER_OTLP_ENDPOINT   例如 http://otel-collector:4318
      OTEL_EXPORTER_OTLP_HEADERS    例如 Langfuse 的 Authorization=Basic ...
      OTEL_SDK_DISABLED=true        完全关闭
    没配 endpoint 时仍会生成 trace_id（写进日志和响应头），只是不导出。
    """
    if os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        return
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return  # 已初始化（例如测试里多次创建 app）

    resource = Resource.create({
        "service.name": SERVICE_NAME,
        "service.version": os.getenv("APP_VERSION", "dev"),
        "deployment.environment": os.getenv("APP_ENV", "development"),
    })
    provider = TracerProvider(resource=resource)

    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        logger.info("OpenTelemetry: exporting traces via OTLP/HTTP")
    elif os.getenv("OTEL_TRACES_CONSOLE", "false").lower() == "true":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)


def shutdown_tracing() -> None:
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()


def current_trace_id() -> Optional[str]:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


# ── 结构化日志 ────────────────────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """一行一个 JSON，带 trace_id / span_id，方便 Loki、Azure Log Analytics 等检索。"""

    def format(self, record: logging.LogRecord) -> str:
        entry: Dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            entry["trace_id"] = format(ctx.trace_id, "032x")
            entry["span_id"] = format(ctx.span_id, "016x")
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging() -> None:
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    if os.getenv("LOG_FORMAT", "text").lower() == "json":
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logging.basicConfig(level=level, handlers=[handler], force=True)
    else:
        logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
