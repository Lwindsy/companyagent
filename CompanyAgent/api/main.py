"""
CompanyAgent 智能客服系统 — FastAPI 入口

启动时打印小熊饼干图案。
所有核心组件在 lifespan 中初始化，通过环境变量配置。
"""
import asyncio
import logging
import os
import pathlib
import secrets
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional


_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import uvicorn
from dotenv import load_dotenv
from opentelemetry import trace
from fastapi import Depends, FastAPI, HTTPException, Request, Response, Security, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

load_dotenv()

from observability.telemetry import (
    CHAT_LATENCY, CHAT_REQUESTS, HTTP_LATENCY, HTTP_REQUESTS,
    configure_logging, current_trace_id, instrument_anthropic, setup_tracing, shutdown_tracing, tracer,
)

# 可观测性：日志格式、OpenTelemetry、LLM 调用埋点都要在任何组件创建之前完成。
configure_logging()
setup_tracing()
instrument_anthropic()
logger = logging.getLogger(__name__)

BANNER = r"""
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
   ╔══════════════════════╗
   ║  CompanyAgent  v2.0  ║
   ║   智能客服 AI 系统    ║
   ╚══════════════════════╝
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
"""

# ── 全局组件（lifespan 中初始化）─────────────────────────────────────────────
_orchestrator = None
_memory       = None
_tool_manager = None
_monitor      = None
_evaluator    = None
_skill_manager = None
# Optional account operations are separate from general-support memory/orchestration.
# None means no ready workflow; the request-time provider below enforces readiness.
_business_workflow = None
# The optional agent shares business identity but owns its graph/checkpoint lifetime.
_agent_engine = None
_admin_sessions: Dict[str, float] = {}
_ADMIN_SESSION_TTL_SECONDS = 8 * 60 * 60


def _admin_credentials() -> tuple[str, str]:
    """Read admin credentials from the environment. There is no built-in password."""
    return (
        os.getenv("ADMIN_USERNAME", "admin"),
        os.getenv("ADMIN_PASSWORD", ""),
    )


# Declared as an OpenAPI security scheme so Swagger UI shows an "Authorize" button.
_admin_bearer = HTTPBearer(auto_error=False, description="Token from POST /admin/login")


def _require_admin(credentials: Optional[HTTPAuthorizationCredentials] = Security(_admin_bearer)) -> str:
    """Validate a short-lived bearer token issued by the admin login endpoint."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(401, "Administrator authentication is required")
    token = credentials.credentials.strip()
    expires_at = _admin_sessions.get(token, 0)
    if expires_at <= time.time():
        _admin_sessions.pop(token, None)
        raise HTTPException(401, "Administrator session has expired")
    return token

def _anthropic_cfg() -> Dict[str, Any]:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    cfg: Dict[str, Any] = {
        "api_key":  key,
        "model":    os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022").strip(),
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        cfg["base_url"] = base_url
    return cfg


def _business_enabled() -> bool:
    # Environment values are strings; only case-insensitive "true" opts in. Leaving
    # this unset preserves existing support behavior and other backend compatibility.
    return os.getenv("BUSINESS_WORKFLOWS_ENABLED", "false").lower() == "true"


def _demo_approval_enabled() -> bool:
    # Simulated customer self-approval is separately gated from administrator access.
    return os.getenv("DEMO_SELF_APPROVAL", "false").lower() == "true"


def _get_business_workflow():
    # Disabled is 404; enabled but uninitialized is 503. Routes use this callback
    # at request time instead of assuming that startup has made the feature ready.
    if not _business_enabled():
        raise HTTPException(404, "Business workflows are disabled")
    if _business_workflow is None:
        raise HTTPException(503, "Business workflows are not ready")
    return _business_workflow


def _get_agent_engine():
    # A disabled runtime can retain a read-only store for previous runs. Creation
    # and continuation have separate gates; rollback must not erase saved status.
    if _agent_engine is None:
        status = 503 if os.getenv("AGENT_RUNTIME_ENABLED", "false").lower() == "true" else 404
        raise HTTPException(status, "Account agent is not ready")
    return _agent_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator, _memory, _tool_manager, _monitor, _evaluator, _skill_manager, _business_workflow, _agent_engine

    print(BANNER, flush=True)

    from agents.agent_orchestrator import AgentOrchestrator
    from core.intent_recognizer import IntentRecognizer
    from evaluation.evaluator import EndToEndEvaluator
    from retrieval.knowledge_base import KnowledgeBase
    from retrieval.tool_manager import MCPToolManager, Tool
    from memory.conversation_memory import MemoryManager
    from monitor.performance_monitor import PerformanceMonitor
    from core.skill_loader import SkillManager

    cfg = _anthropic_cfg()
    logger.info(f"Model: {cfg['model']}  base_url: {cfg.get('base_url', '(official)')}")

    business_client = None
    _business_workflow = None
    if _business_enabled():
        # Initialize optional tables before accepting sessions. Blocking database work
        # runs in a thread so the async server can keep servicing other connections.
        from anthropic import AsyncAnthropic
        from business.db import Database
        from business.service import BusinessService
        from workflows.anthropic_extractor import AnthropicExtractor
        from workflows.service import WorkflowService
        from workflows.sessions import SessionService

        db = Database(os.getenv("BUSINESS_DB_PATH", str(pathlib.Path(_ROOT) / "data/business/business.db")))
        await asyncio.to_thread(db.initialize)
        # No hidden SDK retries: one extraction attempt sends one model request.
        # The model timeout does not mean a later database thread is safely cancelled.
        business_client = AsyncAnthropic(api_key=cfg["api_key"], base_url=cfg.get("base_url"), max_retries=0, timeout=20)
        business = BusinessService(db)
        sessions = SessionService(business, sessions_per_ip_hour=int(os.getenv("BUSINESS_SESSIONS_PER_IP_HOUR", "10")))
        _business_workflow = WorkflowService(business, AnthropicExtractor(business_client, cfg["model"]), sessions)
        # Existing Redis/Chroma startup below is still needed by general support.
        # Independence of business modules does not remove whole-application dependencies.

    # 意图识别器（Orchestrator 内部也会创建，这里单独暴露给 Evaluator）
    recognizer = IntentRecognizer(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    # Skills：启动时从目录加载业务能力说明，并在 Agent 调用 LLM 时动态注入。
    skills_dir = os.getenv("COMPANYAGENT_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills"))
    _skill_manager = SkillManager(
        root_dir=skills_dir,
        max_prompt_chars=int(os.getenv("COMPANYAGENT_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    _skill_manager.load()

    # Agent 编排器
    _orchestrator = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=_skill_manager,
    )

    # 记忆管理器（Redis 工作记忆 + ChromaDB 情景记忆/用户画像）
    _memory = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    # MCP 工具管理器 + RAG 知识库（基于 ChromaDB 的真实检索）
    _tool_manager = MCPToolManager(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )
    kb = KnowledgeBase(
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
    )
    logger.info(f"Knowledge base loaded: {await kb.doc_count_async()} document chunks")

    def knowledge_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        query = params.get("query", "")
        return [{
            "title": "Knowledge Base Fallback Result",
            "content": f"The knowledge base is temporarily unavailable, so semantic search for “{query}” could not be completed. Please try again later or contact human support.",
            "score": 0.0,
            "fallback": True,
            "error": error,
        }]

    _tool_manager.register(Tool(
        name="knowledge_search",
        description="Search the knowledge base using ChromaDB vector retrieval.",
        handler=kb.search_handler,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            "required": ["query"],
        },
        cache_ttl=300.0,
        supports_rerank=True,
        fallback=knowledge_fallback,
    ))

    # 性能监控（可选启动 Prometheus）
    prom_port = int(os.getenv("PROMETHEUS_PORT", "0")) or None
    _monitor = PerformanceMonitor(
        orchestrator=_orchestrator,
        tool_manager=_tool_manager,
        interval_s=float(os.getenv("MONITOR_INTERVAL", "10")),
        webhook_url=os.getenv("ALERT_WEBHOOK_URL") or None,
        prometheus_port=prom_port,
    )
    await _monitor.start()

    # 评测器
    _evaluator = EndToEndEvaluator(
        orchestrator=_orchestrator,
        recognizer=recognizer,
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        baseline_path=os.getenv("EVAL_BASELINE_PATH", "/app/data/eval/baseline.json"),
    )

    logger.info("Service is ready")
    _agent_engine = None
    agent_enabled = os.getenv("AGENT_RUNTIME_ENABLED", "false").lower() == "true"
    saved_agent_runs = False
    if _business_workflow is not None:
        with _business_workflow.business.db.connect() as conn:
            saved_agent_runs = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_runs'").fetchone() is not None
    if (agent_enabled or saved_agent_runs) and _business_workflow is not None:
        # Disabling the prerequisite business capability also disables this optional
        # consumer. It must not prevent the pre-existing general support from starting.
        from agent_runtime.engine import AgentEngine
        from agent_runtime.model import AnthropicModel
        from agent_runtime.store import RunStore
        from agent_runtime.tools import Gateway
        from agent_runtime.mcp_client import DiagnosticClient

        async def agent_knowledge(query):
            # Use existing retrieval without its extra LLM rewrite/rerank calls.
            # Every chargeable new-agent call must go through its model budget.
            result = await _tool_manager.call("knowledge_search", {"query": query, "top_k": 3})
            return {"success": result.success, "data": result.data}

        store = RunStore(_business_workflow.business.db)
        gateway = Gateway(_business_workflow.business, store, DiagnosticClient(), agent_knowledge)
        model = AnthropicModel(business_client, cfg["model"],
                               float(os.getenv("AGENT_INPUT_USD_PER_MILLION", "2")),
                               float(os.getenv("AGENT_OUTPUT_USD_PER_MILLION", "10")))
        checkpoint = os.getenv("AGENT_CHECKPOINT_PATH", _business_workflow.business.db.path + ".agent-checkpoints")
        _agent_engine = await AgentEngine(store, gateway, model, checkpoint).start(worker=agent_enabled)
    try:
        yield
    finally:
        # Stop the queue consumer before closing the provider and checkpoint DB.
        # Interrupted calls retain reservations and journals for explicit recovery.
        if _agent_engine is not None:
            await _agent_engine.close()
        _agent_engine = None

    await _monitor.stop()
    if business_client is not None:
        # Normal shutdown releases this dedicated client's HTTP connections.
        await business_client.close()
    _business_workflow = None
    if _memory is not None:
        await _memory.close()
    shutdown_tracing()
    logger.info("Service shut down")


# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Customer Support API",
    version="2.0.0",
    lifespan=lifespan,
    # Served by the prefix-aware routes below instead of FastAPI's defaults.
    docs_url=None,
    openapi_url=None,
)

# Register routes once for stable OpenAPI; callbacks enforce runtime gates and identity.
# E402 documents this intentional late import rather than changing import order.
from api.business_routes import create_business_router  # noqa: E402
from api.agent_routes import create_agent_router  # noqa: E402

app.include_router(create_business_router(_get_business_workflow, _require_admin, _demo_approval_enabled))
app.include_router(create_agent_router(_get_agent_engine, _get_business_workflow,
                                     lambda: os.getenv("AGENT_RUNTIME_ENABLED", "false").lower() == "true"))


# Behind Caddy/Nginx the API is mounted under /api/python with the prefix stripped.
# The docs page loads the schema with a relative URL, and the schema's server URL comes
# from X-Forwarded-Prefix, so both the page and "Try it out" work locally and behind the proxy.
@app.get("/openapi.json", include_in_schema=False)
async def openapi_schema(request: Request) -> JSONResponse:
    prefix = request.headers.get("x-forwarded-prefix", "").rstrip("/")
    return JSONResponse({**app.openapi(), "servers": [{"url": prefix or "/"}]})


@app.get("/docs", include_in_schema=False)
async def swagger_docs() -> HTMLResponse:
    return get_swagger_ui_html(openapi_url="openapi.json", title=f"{app.title} - Swagger UI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id"],
)


@app.middleware("http")
async def http_metrics(request: Request, call_next):
    """HTTP 指标 + 在响应头返回 trace_id，用户反馈问题时可直接按 trace_id 查链路。"""
    t0 = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        trace_id = current_trace_id()
        if trace_id:
            response.headers["X-Trace-Id"] = trace_id
        return response
    finally:
        # 用路由模板（/knowledge/stats）而不是原始路径做标签，避免标签基数爆炸。
        route = getattr(request.scope.get("route"), "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
        HTTP_LATENCY.labels(request.method, route).observe(time.perf_counter() - t0)


# 外层 span：每个 HTTP 请求一个根 span，业务 span 挂在它下面。健康检查和指标抓取不记录。
FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:     str = Field(min_length=1, max_length=4000)
    user_id:     str = "anonymous"
    conv_id:     Optional[str] = None
    # Legacy anonymous callers may omit this; business messages must provide a stable
    # message ID and reuse it when retrying a potentially completed operation.
    request_id: Optional[str] = Field(default=None, max_length=40, min_length=1, pattern=r"^[A-Za-z0-9_-]+$")


class ChatResponse(BaseModel):
    conv_id:     str
    response:    str
    intent:      str
    intent_group: str = "other"
    agent_type:  str
    agent_types: List[str] = Field(default_factory=list)
    primary_agent: str = ""
    supporting_agents: List[str] = Field(default_factory=list)
    routing_reason: str = ""
    routing_confidence: float = 0.0
    escalated:   bool
    latency_ms:  float
    knowledge_used: bool = False
    entities: Dict[str, List[str]] = Field(default_factory=dict)
    intent_confidence: float = 0.0
    intent_source_scores: Dict[str, float] = Field(default_factory=dict)
    # Defaults preserve old callers. A list default_factory creates a fresh list
    # for each response rather than sharing mutable data across different requests.
    mode: str = "workflow"
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    handoff: Optional[Dict[str, Any]] = None
    operation_id: Optional[str] = None


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminLoginResponse(BaseModel):
    access_token: str
    expires_in: int
    username: str


# ── 路由 ──────────────────────────────────────────────────────────────────────
@app.post("/admin/login", response_model=AdminLoginResponse, tags=["Administration"])
async def admin_login(credentials: AdminLoginRequest):
    """Authenticate an administrator and return a short-lived bearer token."""
    expected_username, expected_password = _admin_credentials()
    if not expected_password:
        raise HTTPException(401, "Administrator sign-in is disabled: ADMIN_PASSWORD is not configured")
    valid = (
        secrets.compare_digest(credentials.username, expected_username)
        and secrets.compare_digest(credentials.password, expected_password)
    )
    if not valid:
        raise HTTPException(401, "Invalid administrator username or password")

    token = secrets.token_urlsafe(32)
    _admin_sessions[token] = time.time() + _ADMIN_SESSION_TTL_SECONDS
    return AdminLoginResponse(
        access_token=token,
        expires_in=_ADMIN_SESSION_TTL_SECONDS,
        username=expected_username,
    )


@app.get("/admin/overview", tags=["Administration"])
async def admin_overview(_: str = Depends(_require_admin)):
    """Return the concise operational summary used by the administrator workspace."""
    if _orchestrator is None:
        raise HTTPException(503, "Service is not ready")
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    kb_chunks = await tool.handler.__self__.doc_count_async() if tool else 0
    monitor = _monitor.summary() if _monitor else {}
    return {
        "status": "ok",
        "agent_stats": _orchestrator.get_stats(),
        "knowledge_chunks": kb_chunks,
        "alerts": monitor.get("alerts", monitor.get("active_alerts", [])),
    }


@app.get("/health")
async def health():
    if _orchestrator is None:
        raise HTTPException(503, "Service is not ready")
    capabilities = []
    # The frontend exposes optional controls only if the feature is enabled AND ready.
    # Merely selecting the Python backend is not proof these APIs are available.
    if _business_enabled() and _business_workflow is not None:
        capabilities = ["business_workflows", "approvals"]
        if _demo_approval_enabled():
            capabilities.append("demo_self_approval")
        if _agent_engine is not None and os.getenv("AGENT_RUNTIME_ENABLED", "false").lower() == "true":
            capabilities.append("account_agent")
    return {"status": "ok", "agents": _orchestrator.get_stats(), "capabilities": capabilities}


@app.get("/skills", tags=["Skills"])
async def skills_summary(_: str = Depends(_require_admin)):
    """Return loaded skills to verify hot reload and diagnose parse errors."""
    if _skill_manager is None:
        raise HTTPException(503, "Skills are not initialized")
    return _skill_manager.summary()


@app.post("/skills/reload", tags=["Skills"])
async def reload_skills(_: str = Depends(_require_admin)):
    """Rescan the skill directory at runtime without restarting the service."""
    if _skill_manager is None:
        raise HTTPException(503, "Skills are not initialized")
    _skill_manager.reload()
    if _orchestrator is not None:
        _orchestrator.set_skill_manager(_skill_manager)
    return _skill_manager.summary()


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    """
    主对话接口。完整流程：
      记忆读取 → 意图识别 → Agent 路由 → 执行 → 记忆写入
    """
    authorization = request.headers.get("authorization")
    # Any Authorization header selects the customer account path. Invalid tokens fail
    # explicitly instead of being silently downgraded to the anonymous support path.
    if authorization is not None:
        from api.business_routes import http_error
        from business.models import BusinessError

        workflow = _get_business_workflow()
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(401, "Customer authentication required")
        try:
            session = await asyncio.to_thread(workflow.sessions.authenticate, parts[1])
            # The authenticated session selects the account; req.user_id is ignored,
            # so a forged JSON body cannot switch ownership of a financial operation.
            if not req.conv_id or not req.request_id or not req.message.strip():
                raise HTTPException(422, "Business messages require conv_id, request_id and non-empty text")
            return await workflow.chat(session, req.message, req.conv_id, req.request_id)
        except BusinessError as error:
            raise http_error(error) from error

    # Only no-Authorization requests reach the pre-existing anonymous workflow below.
    # Account operations do not read its client-selected conversation-memory namespace.
    if _orchestrator is None or _memory is None:
        raise HTTPException(503, "Service is not ready")

    from agents.agent_orchestrator import Request as OrcReq
    from memory.conversation_memory import MsgRole

    t0 = time.perf_counter()
    conv_id = req.conv_id or str(uuid.uuid4())
    root_span = trace.get_current_span()
    root_span.set_attribute("companyagent.conv_id", conv_id)

    # 1. 读取记忆上下文
    with tracer.start_as_current_span("memory.get_context"):
        mem_ctx = await _memory.get_context(req.user_id, conv_id, query=req.message)

    # 2. 构建编排请求（含对话历史，用于意图识别上下文）
    history = [
        {"role": m.role.value, "content": m.content}
        for m in mem_ctx.recent_messages[-5:]
    ] if mem_ctx.recent_messages else None

    intent_result = await _orchestrator.recognize_intent(req.message, history=history)
    with tracer.start_as_current_span("rag.retrieve") as rag_span:
        knowledge_text, knowledge_used = await _build_knowledge_context(req.message, intent=intent_result.intent)
        rag_span.set_attribute("companyagent.rag.used", knowledge_used)
    context_parts = [mem_ctx.to_prompt_text()]
    if knowledge_text:
        context_parts.append(knowledge_text)
    full_context = "\n\n".join(part for part in context_parts if part)

    orch_req = OrcReq(
        message=req.message,
        user_id=req.user_id,
        conv_id=conv_id,
        context=full_context,
        history=history,
        entities=intent_result.entities,
        intent=intent_result.intent,
        intent_group=intent_result.intent_group,
        urgency=intent_result.urgency,
        intent_confidence=intent_result.confidence,
    )

    # 3. 执行
    result = await _orchestrator.run(orch_req)

    # 4. 写入记忆
    with tracer.start_as_current_span("memory.write"):
        await _memory.add_message(req.user_id, conv_id, MsgRole.USER, req.message)
        await _memory.add_message(req.user_id, conv_id, MsgRole.ASSISTANT, result.response)

    # 5. 异步更新用户画像（不阻塞响应）
    asyncio.create_task(_memory.update_profile(req.user_id, conv_id))

    agent = result.agent_type.value
    root_span.set_attribute("companyagent.intent", result.intent.value if result.intent else "other")
    root_span.set_attribute("companyagent.agent", agent)
    root_span.set_attribute("companyagent.escalated", result.escalated)
    CHAT_REQUESTS.labels(agent, intent_result.intent_group, str(result.escalated).lower(), str(knowledge_used).lower()).inc()
    CHAT_LATENCY.labels(agent).observe(time.perf_counter() - t0)

    return ChatResponse(
        conv_id=conv_id,
        response=result.response,
        intent=result.intent.value if result.intent else "other",
        intent_group=intent_result.intent_group,
        agent_type=result.agent_type.value,
        agent_types=[agent_type.value for agent_type in result.agent_types],
        primary_agent=result.primary_agent.value if result.primary_agent else result.agent_type.value,
        supporting_agents=[agent_type.value for agent_type in result.supporting_agents],
        routing_reason=result.routing_reason,
        routing_confidence=result.routing_confidence,
        escalated=result.escalated,
        latency_ms=round(result.latency_ms, 1),
        knowledge_used=knowledge_used,
        entities=intent_result.entities,
        intent_confidence=round(intent_result.confidence, 4),
        intent_source_scores=intent_result.source_scores,
    )


async def _build_knowledge_context(message: str, intent=None, top_k: int = 3) -> tuple[str, bool]:
    """
    为 /chat 主链路构建 RAG 知识上下文。

    这里复用 MCPToolManager 的查询改写、并行召回、重排、fallback 能力。
    """
    if _tool_manager is None:
        return "", False
    if not _should_use_knowledge(message, intent=intent):
        return "", False
    try:
        result = await _tool_manager.search_with_rewrite("knowledge_search", message, top_k=top_k)
        if not result.success or not isinstance(result.data, list) or not result.data:
            return "", False

        parts = ["[Knowledge Base Search Results]"]
        used = False
        for i, item in enumerate(result.data[:top_k], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "Untitled Document"))
            content = str(item.get("content", "")).strip()
            score = item.get("score", "")
            if not content:
                continue
            used = True
            parts.append(f"{i}. Title: {title}\n   Relevance: {score}\n   Content: {content[:600]}")

        if not used:
            return "", False
        parts.append("Prioritize the knowledge-base content above. If it is insufficient, supplement it with general customer-support guidance.")
        return "\n".join(parts), True
    except Exception as ex:
        logger.warning(f"Failed to build knowledge-base context: {ex}")
        return "", False


def _should_use_knowledge(message: str, intent=None) -> bool:
    """Skip knowledge retrieval for greetings to avoid irrelevant RAG context."""
    msg = (message or "").strip().lower()
    if not msg:
        return False
    intent_value = getattr(intent, "value", intent)
    if intent_value in {"greeting", "feedback", "escalation", "human_handoff", "other"}:
        return False
    if intent_value in {
        "query", "request", "technical", "billing", "account", "complaint",
        "order_status", "logistics", "refund", "invoice", "payment_issue",
        "account_security", "technical_login", "technical_crash",
    }:
        return True
    greetings = {"hi", "hello", "hey", "good morning", "good evening"}
    if msg in greetings:
        return False
    business_keywords = [
        "refund", "return", "order", "shipping", "delivery", "invoice", "charge", "payment", "billing", "subscription",
        "login", "error", "crash", "membership", "points", "account", "password", "address",
        "refund", "order", "invoice", "payment", "error", "login",
    ]
    return len(msg) >= 4 or any(kw in msg for kw in business_keywords)


@app.get("/monitor")
async def monitor_summary(_: str = Depends(_require_admin)):
    """Return real-time monitoring: agent success rates, tool statistics, alerts, and recommendations."""
    if _monitor is None:
        raise HTTPException(503, "Service is not ready")
    return _monitor.summary()


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus metrics endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/search")
async def search(query: str, top_k: int = 5, _: str = Depends(_require_admin)):
    """Run optimized retrieval: query rewriting, parallel recall, reranking, and Top-K selection."""
    if _tool_manager is None:
        raise HTTPException(503, "Service is not ready")
    result = await _tool_manager.search_with_rewrite("knowledge_search", query, top_k=top_k)
    return {"query": query, "results": result.data, "reranked": result.reranked}


class DocInput(BaseModel):
    """Input for a single document."""
    title:   str
    content: str


class BatchDocInput(BaseModel):
    """Request body for importing multiple documents."""
    documents: List[DocInput]


class EvalIntentInput(BaseModel):
    """Intent-classification evaluation case."""
    message: str
    expected_intent: str
    context: Optional[Dict[str, Any]] = None


class EvalDialogInput(BaseModel):
    """Conversation-quality evaluation case: question is single-turn; turns is multi-turn."""
    question: Optional[str] = None
    turns: Optional[List[str]] = None
    user_id: Optional[str] = None
    conv_id: Optional[str] = None


class EvalRunInput(BaseModel):
    """Evaluation request. Uses built-in defaults when empty."""
    intent_cases: Optional[List[EvalIntentInput]] = None
    dialog_cases: Optional[List[EvalDialogInput]] = None


@app.post("/knowledge/add", tags=["Knowledge Base"])
async def add_knowledge(body: BatchDocInput, _: str = Depends(_require_admin)):
    """Import documents into the knowledge base.

    Documents are automatically split into 500-character chunks and stored in ChromaDB, whose built-in embedding model vectorizes them.

    Example request body:
    ```json
    {
      "documents": [
        {"title": "Refund Policy", "content": "Customers may request a no-reason refund within 7 days of purchase..."},
        {"title": "Delivery Information", "content": "Standard delivery takes 3–5 business days..."}
      ]
    }
    ```
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "Knowledge base is not initialized")
    kb = tool.handler.__self__
    count = await kb.add_documents_async([{"title": d.title, "content": d.content} for d in body.documents])
    total = await kb.doc_count_async()
    return {"message": f"Successfully imported {count} document chunks", "added_chunks": count, "total_chunks": total}


@app.post("/knowledge/upload", tags=["Knowledge Base"])
async def upload_knowledge(file: UploadFile = File(...), _: str = Depends(_require_admin)):
    """Import a file into the knowledge base.

    Supported formats:
    - `.txt` / `.md`: the full file becomes one document and its filename becomes the title
    - `.json`: a JSON array in the form `[{"title": "...", "content": "..."}, ...]`

    File size limit: 10 MB.
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "Knowledge base is not initialized")
    kb = tool.handler.__self__

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "File size exceeds the 10 MB limit")

    text = content.decode("utf-8", errors="ignore")
    filename = file.filename or "unknown"

    if filename.endswith(".json"):
        import json as _json
        try:
            docs = _json.loads(text)
            if not isinstance(docs, list):
                raise HTTPException(400, "A JSON file must be an array: [{title, content}, ...]")
        except _json.JSONDecodeError as e:
            raise HTTPException(400, f"Failed to parse JSON: {e}")
    else:
        # txt / md：整个文件作为一篇文档
        title = filename.rsplit(".", 1)[0] if "." in filename else filename
        docs = [{"title": title, "content": text}]

    count = await kb.add_documents_async(docs)
    total = await kb.doc_count_async()
    return {
        "message": f"Successfully imported {filename}",
        "added_chunks": count,
        "total_chunks": total,
    }


@app.get("/knowledge/stats", tags=["Knowledge Base"])
async def knowledge_stats(_: str = Depends(_require_admin)):
    """Return knowledge-base statistics, including total document chunks."""
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "Knowledge base is not initialized")
    kb = tool.handler.__self__
    return {"total_chunks": await kb.doc_count_async()}


@app.post("/eval/run")
async def run_eval(body: Optional[EvalRunInput] = None, _: str = Depends(_require_admin)):
    """Run built-in evaluation cases and return an evaluation report."""
    if _evaluator is None:
        raise HTTPException(503, "Service is not ready")
    from evaluation.evaluator import DEFAULT_DIALOG_CASES, DEFAULT_INTENT_CASES, IntentTestCase

    if body and body.intent_cases is not None:
        intent_cases = [
            IntentTestCase(
                message=c.message,
                expected_intent=c.expected_intent,
                context=c.context,
            )
            for c in body.intent_cases
        ]
    else:
        intent_cases = DEFAULT_INTENT_CASES

    if body and body.dialog_cases is not None:
        dialog_cases = [
            c.model_dump(exclude_none=True)
            for c in body.dialog_cases
        ]
    else:
        dialog_cases = DEFAULT_DIALOG_CASES

    report = await _evaluator.run(
        intent_cases=intent_cases,
        dialog_cases=dialog_cases,
    )
    return {
        "pass_rate":       report.pass_rate,
        "total":           report.total,
        "passed":          report.passed,
        "avg_scores":      report.avg_scores,
        "regressions":     report.regressions,
        "recommendations": report.recommendations,
        "results": [
            {
                "test_id": r.test_id,
                "passed": r.passed,
                "scores": r.scores,
                "detail": r.detail,
                "metadata": r.metadata,
            }
            for r in report.results
        ],
    }


# ── 交互式 CLI ────────────────────────────────────────────────────────────────
async def _cli():
    print(BANNER)
    print("Customer Support CLI — enter quit to exit\n")

    from agents.agent_orchestrator import AgentOrchestrator, Request
    from memory.conversation_memory import MemoryManager, MsgRole
    from core.skill_loader import SkillManager

    cfg = _anthropic_cfg()
    skill_manager = SkillManager(
        root_dir=os.getenv("COMPANYAGENT_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills")),
        max_prompt_chars=int(os.getenv("COMPANYAGENT_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    skill_manager.load()
    orch = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=skill_manager,
    )
    mem  = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "localhost"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/tmp/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    user_id, conv_id = "cli_user", str(uuid.uuid4())

    while True:
        try:
            msg = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye ʕ•ᴥ•ʔ")
            break
        if not msg or msg.lower() in ("quit", "exit"):
            print("Goodbye ʕ•ᴥ•ʔ")
            break

        ctx = await mem.get_context(user_id, conv_id, query=msg)
        history = [
            {"role": m.role.value, "content": m.content}
            for m in ctx.recent_messages[-5:]
        ] if ctx.recent_messages else None
        req = Request(message=msg, user_id=user_id, conv_id=conv_id, context=ctx.to_prompt_text(), history=history)
        result = await orch.run(req)

        await mem.add_message(user_id, conv_id, MsgRole.USER, msg)
        await mem.add_message(user_id, conv_id, MsgRole.ASSISTANT, result.response)

        print(f"\nCompanyAgent [{result.agent_type.value}]: {result.response}\n")

    await mem.close()


if __name__ == "__main__":
    if "--cli" in sys.argv:
        asyncio.run(_cli())
    else:
        uvicorn.run(
            "api.main:app",
            host=os.getenv("API_HOST", "0.0.0.0"),
            port=int(os.getenv("API_PORT", "8000")),
            reload=os.getenv("APP_ENV") == "development",
        )
