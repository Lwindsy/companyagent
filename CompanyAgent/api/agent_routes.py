"""Authenticated optional run API; every read/resume/cancel rechecks ownership.

The UI grants one named full-payment/renewal capability explicitly. Neither model
arguments nor free-form claims of staff approval can create or broaden that grant.
"""
import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.business_routes import http_error
from business.models import BusinessError


class Grant(BaseModel):
    """Customer-authorized structured action; never derived from tool output."""
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["refund", "renewal"]
    charge_id: str | None = Field(default=None, max_length=80)
    cancel_at_period_end: bool | None = None

    @model_validator(mode="after")
    def target(self):
        # Exact target binding prevents granting one payment and writing another.
        # Reading note: self contains the parsed grant. A refund needs only a nonempty
        # charge_id; renewal needs only a boolean cancellation target. Returning self keeps
        # the validated object. Mixed or missing targets raise ValueError, which
        # FastAPI/Pydantic converts to a validation error before any tool execution.
        if self.kind == "refund" and (not self.charge_id or self.cancel_at_period_end is not None):
            raise ValueError("A full refund grant needs exactly one saved payment")
        if self.kind == "renewal" and (self.charge_id is not None or self.cancel_at_period_end is None):
            raise ValueError("A renewal grant needs exactly one boolean target")
        return self


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conv_id: str = Field(min_length=1, max_length=80)
    request_id: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=4000)
    write_grant: Grant | None = None


class ContinueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=4000)
    version: int = Field(ge=0)
    write_grant: Grant | None = None


def create_agent_router(provider, workflow, enabled=lambda: True):
    # Reading note: provider returns the runtime, workflow returns authenticated sessions, and
    # enabled reports whether NEW execution is allowed. These injected callbacks avoid
    # importing the application module in tests. Return a router containing authenticated
    # create/read/events/resume/cancel handlers; saved reads can remain available when
    # execution is switched off.
    """Inject runtime/identity callbacks instead of importing api.main in tests."""
    router = APIRouter(tags=["Account Agent"])
    bearer = HTTPBearer(auto_error=False)

    async def customer(credentials: HTTPAuthorizationCredentials | None = Security(bearer)):
        # Require an available runtime, including its read-only rollback mode.
        # Creation/resumption have a separate gate below so disabling new work
        # does not hide saved results or prevent an authenticated cancellation.
        # Reading note: FastAPI supplies the optional HTTP Bearer credentials through
        # Security. Require a runtime, reject a missing token, then authenticate via the
        # existing session service in a thread so synchronous SQLite work does not block the
        # event loop. Return trusted session scope or translate a BusinessError into its
        # public HTTP error.
        provider()
        if credentials is None:
            raise HTTPException(401, "Customer authentication required")
        try:
            return await asyncio.to_thread(workflow().sessions.authenticate, credentials.credentials)
        except BusinessError as error:
            raise http_error(error) from error

    @router.post("/customer/agent/runs")
    async def create(body: RunInput, session=Depends(customer)):
        # Reading note: body is validated RunInput and session comes from the authentication
        # dependency, not model JSON. Check the new-run flag, matching conversation and
        # nonblank message, then store the stable request envelope and exact optional grant.
        # Return public queued/replayed status. The worker, not this HTTP handler, purchases
        # inference.
        if not enabled():
            raise HTTPException(404, "New agent runs are disabled; saved runs remain readable")
        if body.conv_id != session.conv_id:
            raise HTTPException(404, "Conversation not found")
        if not body.message.strip():
            raise HTTPException(422, "Message must contain text")
        engine = provider()
        try:
            row = engine.store.create(session, body.request_id, body.message.strip(),
                                      grant=body.write_grant.model_dump(exclude_none=True) if body.write_grant else None)
            return engine.store.public(row["id"], session)
        except BusinessError as error:
            raise http_error(error) from error

    @router.get("/customer/agent/runs/{run_id}")
    async def get(run_id: str, session=Depends(customer)):
        # Reading note: run_id names a saved run and session restricts ownership. Fetch its
        # public status, then read current business actions again so a stopped/cancelled graph
        # cannot conceal an operation that committed later. Return model text and ledger facts
        # separately; propagate only mapped public business errors.
        try:
            engine = provider()
            result = engine.store.public(run_id, session)
            # Reconcile fresh action facts even after cancellation/timeout: stopping
            # the graph cannot roll back a database thread that already committed.
            result["result"]["actions"] = await asyncio.to_thread(engine.gateway.business.list_actions,
                                                                 session.scope, session.conv_id)
            return result
        except BusinessError as error:
            raise http_error(error) from error

    @router.get("/customer/agent/runs/{run_id}/events")
    async def events(run_id: str, after: int = 0, stream: bool = False, session=Depends(customer)):
        # Reading note: after is the last seen sequence; stream=false returns a bounded JSON
        # page. stream=true wraps the nested async generator in an SSE response without
        # caching. Check run ownership before either branch. The bearer token stays in the
        # header, never the URL.
        engine = provider()
        try:
            engine.store.get(run_id, session)
        except BusinessError as error:
            raise http_error(error) from error
        if not stream:
            return {"items": engine.store.events(run_id, max(0, after))}

        async def emit():
            # A finite stream is reconnectable by sequence. Recheck identity every
            # heartbeat so a long-lived connection cannot outlive its session token.
            # Reading note: This async generator yields SSE id/data strings and one-second
            # heartbeats for at most thirty iterations. Recheck stored session expiry each
            # time and advance cursor after each event. A browser can reconnect with the last
            # sequence. Expiry or cancellation ends the stream instead of leaking a long-lived
            # authenticated connection.
            cursor = max(0, after)
            for _ in range(30):
                try:
                    with engine.store.db.connect() as conn:
                        valid = conn.execute("SELECT 1 FROM customer_sessions WHERE token_hash=? AND expires_at>?",
                                             (session.token_hash, engine.store.clock())).fetchone()
                    if not valid:
                        return
                    for event in engine.store.events(run_id, cursor):
                        cursor = event["seq"]
                        yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(1)
                except asyncio.CancelledError:
                    return
        return StreamingResponse(emit(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @router.post("/customer/agent/runs/{run_id}/input")
    async def resume(run_id: str, body: ContinueInput, session=Depends(customer)):
        # Reading note: body carries a stable continuation ID, observed version, nonblank
        # message and optional fresh exact grant. Reject when execution is disabled, then let
        # the transactional store require waiting_user and enforce replay/quota rules. No
        # field can approve a staff action. Return the existing run public status after
        # queueing.
        if not enabled():
            raise HTTPException(404, "Agent continuation is disabled")
        if not body.message.strip():
            raise HTTPException(422, "Message must contain text")
        engine = provider()
        try:
            engine.store.resume(run_id, session, body.request_id, body.message.strip(), body.version,
                                body.write_grant.model_dump(exclude_none=True) if body.write_grant else None)
            return engine.store.public(run_id, session)
        except BusinessError as error:
            raise http_error(error) from error

    @router.post("/customer/agent/runs/{run_id}/cancel")
    async def cancel(run_id: str, session=Depends(customer)):
        # Reading note: Authenticated session and run_id scope cancellation. Ask the store to
        # stop future work and return its public reconciliation status. Errors are mapped to
        # HTTP responses; an in-flight transaction may still complete and is shown by the
        # separate GET action facts.
        try:
            return provider().store.cancel(run_id, session)
        except BusinessError as error:
            raise http_error(error) from error

    return router
