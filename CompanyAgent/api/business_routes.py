"""HTTP adapter for the independent deterministic business workflow."""
import asyncio
from typing import Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from business.models import BusinessError
from business.service import PERSONAS
from workflows.service import WorkflowService


class SessionInput(BaseModel):
    # Pydantic validates HTTP JSON before the handler runs. Rejecting extra keys
    # prevents this input from accepting caller-chosen identity or approval options.
    model_config = ConfigDict(extra="forbid")
    persona_id: str = Field(max_length=80)


class BusinessChatInput(BaseModel):
    # conv_id belongs to the authenticated session. request_id identifies one message
    # across retries; its bounded size also keeps the resulting domain operation ID small.
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)
    conv_id: str = Field(min_length=1, max_length=80)
    request_id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")


class DecisionInput(BaseModel):
    # Literal restricts the decision vocabulary; domain rules determine when a note
    # is mandatory and whether an authenticated decision is still eligible.
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=2000)


def http_error(error: BusinessError) -> HTTPException:
    # Adapt a framework-independent exception to HTTP: status number, human detail,
    # and a stable code header. A CLI can instead catch BusinessError directly.
    return HTTPException(error.status, str(error), headers={"X-Business-Error": error.code})


def create_business_router(provider: Callable[[], WorkflowService], require_admin: Callable,
                           demo_enabled: Callable[[], bool]) -> APIRouter:
    # Inject host callbacks instead of starting another app: provider checks workflow
    # readiness, require_admin checks staff identity, and demo_enabled gates self-approval.
    router = APIRouter(tags=["Business workflows"])
    bearer = HTTPBearer(auto_error=False, description="Demo customer token")

    async def customer(credentials: HTTPAuthorizationCredentials | None = Security(bearer)):
        # FastAPI runs this dependency first. Scope comes from the stored bearer
        # session, never from the JSON body or a model's suggested customer ID.
        if credentials is None:
            raise HTTPException(401, "Customer authentication required")
        try:
            return await asyncio.to_thread(provider().sessions.authenticate, credentials.credentials)
        except BusinessError as error:
            raise http_error(error) from error

    @router.get("/customer/personas")
    async def personas():
        # Calling provider also enforces the feature gate before showing demo choices.
        provider()
        return {"items": list(PERSONAS)}

    @router.post("/customer/sessions")
    async def create_session(body: SessionInput, request: Request):
        # Use the direct connection peer for limits, not untrusted forwarded headers.
        # Visitors behind a reverse proxy can consequently share a creation allowance.
        try:
            peer = request.client.host if request.client else "unknown"
            return await asyncio.to_thread(provider().sessions.create, body.persona_id, peer)
        except BusinessError as error:
            raise http_error(error) from error

    @router.post("/customer/chat")
    async def business_chat(body: BusinessChatInput, session=Depends(customer)):
        # Whitespace passes a string-length check but is not a meaningful message.
        if not body.message.strip():
            raise HTTPException(422, "Message must contain text")
        try:
            return await provider().chat(session, body.message, body.conv_id, body.request_id)
        except BusinessError as error:
            raise http_error(error) from error

    @router.get("/customer/actions")
    async def customer_actions(conv_id: str, session=Depends(customer)):
        # A valid customer token does not authorize another conversation. Query only
        # the authenticated scope and its server-bound conversation.
        if conv_id != session.conv_id:
            raise HTTPException(404, "Conversation not found")
        return {"items": await asyncio.to_thread(provider().business.list_actions, session.scope, conv_id)}

    @router.post("/customer/demo/actions/{action_id}/decide")
    async def demo_decide(action_id: str, body: DecisionInput, session=Depends(customer)):
        # Demo self-approval needs its own switch and a scope-bound domain call.
        # It does not grant access to another visitor's sandbox or the staff endpoint.
        if not demo_enabled():
            raise HTTPException(404, "Demo approvals are disabled")
        try:
            action = await asyncio.to_thread(provider().business.decide, action_id, body.decision, "demo-supervisor", body.note, session.scope)
            if action["status"] == "expired":
                # The domain already committed expiry. Report conflict afterwards;
                # raising inside its transaction would roll that state update back.
                raise HTTPException(409, "This action has expired")
            return {"action": action}
        except BusinessError as error:
            raise http_error(error) from error

    @router.get("/admin/actions")
    async def admin_actions(_=Depends(require_admin)):
        # Only this administrator dependency permits a cross-customer action query.
        return {"items": await asyncio.to_thread(provider().business.list_actions)}

    @router.post("/admin/actions/{action_id}/decide")
    async def admin_decide(action_id: str, body: DecisionInput, _=Depends(require_admin)):
        # "admin" is an audit label, not authority. The dependency grants access.
        try:
            action = await asyncio.to_thread(provider().business.decide, action_id, body.decision, "admin", body.note)
            if action["status"] == "expired":
                raise HTTPException(409, "This action has expired")
            return {"action": action}
        except BusinessError as error:
            raise http_error(error) from error

    return router
