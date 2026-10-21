"""Loopback-only browser fixture using the real business HTTP router and SQLite.

This module never imports api.main, dotenv, an LLM SDK, Redis or Chroma. Its small
host supplies fixture health/authentication around the unchanged business router.
Every server lifetime owns a new temporary database. Nothing here is mounted by
the production application; /__test__/snapshot and response loss are test tools.
"""

import hashlib
import pathlib
import sys
import tempfile
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Direct script execution starts with tests/ on sys.path; insert CompanyAgent so
# these imports resolve identically in a subprocess and in the pytest suite.
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.business_routes import create_business_router  # noqa: E402
from business.db import Database  # noqa: E402
from business.models import BusinessError  # noqa: E402
from business.service import PERSONAS, BusinessService  # noqa: E402
from workflows.models import ExtractedPlan  # noqa: E402
from workflows.service import WorkflowService  # noqa: E402
from workflows.sessions import SessionService  # noqa: E402


class BrowserFixtureExtractor:
    """Return plans for exact test phrases; this is NOT a language-understanding model.

    message is compared literally. recent/overview preserve the production method
    signature but are unused. Unknown wording clarifies rather than guessing a
    write. calls counts extraction attempts across this single server lifetime.
    """

    def __init__(self):
        self.calls = 0
        self.plans = {persona["starter"]: {"intent": "refund"} for persona in PERSONAS}
        self.plans[PERSONAS[0]["starter"]]["reason_code"] = "duplicate_charge"
        self.plans.update({
            "Cancel renewal.": {"intent": "renewal", "cancel_at_period_end": True},
            "Restore renewal.": {"intent": "renewal", "cancel_at_period_end": False},
            "Check my request status.": {"intent": "status"},
            "Please connect me to a human.": {"intent": "handoff"},
        })

    async def extract(self, message: str, recent: list[dict], overview: dict) -> ExtractedPlan:
        # model_validate uses the real schema before returning a fixed suggestion.
        # No key lookup, network call or arbitrary Python evaluation is performed.
        self.calls += 1
        return ExtractedPlan.model_validate(self.plans.get(message, {"intent": "clarify"}))


def create_app() -> FastAPI:
    """Build an isolated HTTP host; storage is created only when lifespan starts.

    Return a FastAPI app that tests may serve on loopback or enter with TestClient.
    Context exit removes only the directory this fixture itself created, including
    SQLite WAL files. It never accepts an operator-supplied business database path.
    """
    @asynccontextmanager
    async def lifespan(app):
        with tempfile.TemporaryDirectory(prefix="companyagent-browser-fixture-") as folder:
            db = Database(str(pathlib.Path(folder) / "business.db"))
            db.initialize()
            extractor = BrowserFixtureExtractor()
            business = BusinessService(db, clock=lambda: 1_800_000_000)
            # Fixed UTC seconds stabilize charge ages. A larger fixture-only session
            # allowance permits several browser cases/retries from the same loopback IP.
            sessions = SessionService(business, sessions_per_ip_hour=100)
            app.state.workflow = WorkflowService(business, extractor, sessions=sessions)
            app.state.fixture_folder = folder
            app.state.lost_replies = set()
            try:
                yield
            finally:
                # No background database connections survive this service object.
                # TemporaryDirectory handles removal after all request tasks finish.
                app.state.workflow = None

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def workflow():
        # The production router receives this provider through its existing seam.
        return app.state.workflow

    def fixture_admin(authorization: str = Header(default="")):
        # Deliberately public test token, unrelated to real staff accounts/secrets.
        # These tests do not establish production login/JWT behavior.
        if authorization != "Bearer browser-test-supervisor":
            raise HTTPException(401, "Fixture administrator credential required")

    app.include_router(create_business_router(workflow, fixture_admin, lambda: True))
    bearer = HTTPBearer(auto_error=False)

    def fixture_customer(credentials: HTTPAuthorizationCredentials | None = Security(bearer)):
        # Inspection still uses the real session authentication and derived scope.
        # Caller-supplied customer IDs cannot select a different account's ledger.
        if credentials is None:
            raise HTTPException(401, "Fixture customer credential required")
        try:
            return workflow().sessions.authenticate(credentials.credentials)
        except BusinessError as error:
            raise HTTPException(error.status, "Invalid fixture session") from error

    @app.get("/health")
    def health():
        # This is fixture readiness, not production lifespan/health verification.
        return {"status": "ok", "fixture": "offline_business_http_fixture",
                "capabilities": ["business_workflows", "approvals", "demo_self_approval"]}

    @app.get("/__test__/snapshot")
    def snapshot(session=Depends(fixture_customer)):
        # Return committed facts from this authenticated synthetic account only.
        # Fixed table names may be SQL syntax; identity values remain bound data.
        service = workflow()
        counts = {}
        with service.business.db.connect() as conn:
            for table in ("refunds", "actions", "tickets"):
                counts[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE sandbox_id=? AND customer_id=?",
                    (session.scope.sandbox_id, session.scope.customer_id),
                ).fetchone()[0]
            counts["requests"] = conn.execute(
                "SELECT COUNT(*) FROM customer_requests WHERE token_hash=?", (session.token_hash,),
            ).fetchone()[0]
        return {**counts, "overview": service.business.overview(session.scope),
                "extraction_calls": service.extractor.calls}

    @app.middleware("http")
    async def lose_one_committed_reply(request: Request, call_next):
        # Fault injection happens AFTER the real route has completed and committed.
        # One synthetic token loses one chat reply when the test header opts in.
        # A 503 therefore says nothing about rollback; replay must use the same ID.
        response = await call_next(request)
        if (request.url.path == "/customer/chat" and response.status_code == 200
                and request.headers.get("X-Browser-Test-Lose-Reply") == "once"):
            identity = hashlib.sha256(request.headers.get("authorization", "").encode()).hexdigest()
            if identity not in app.state.lost_replies:
                app.state.lost_replies.add(identity)
                return JSONResponse({"detail": "Fixture lost the reply after the business commit"}, status_code=503)
        return response

    return app


if __name__ == "__main__":
    # Keep this tool on the same Python version as CI. There is no host/DB/env-file
    # option, and no call to production startup. Port 8873 belongs to this fixture.
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Use Python 3.12; set BUSINESS_E2E_PYTHON to the existing interpreter")
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8873, access_log=False, log_level="warning")
