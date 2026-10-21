"""A bounded, explicit workflow. No model-directed tool loop."""
import asyncio
import logging
import time

from business.models import BusinessError
from business.service import BusinessService
from workflows.models import ExtractedPlan, Extractor
from workflows.sessions import CustomerSession, SessionService

logger = logging.getLogger(__name__)

# These fixed messages describe database outcomes; no second model call is used to
# invent a payment result. Threshold wording follows this demo's default policy values.
DENIALS = {
    "REFUND_WINDOW_EXPIRED": "This payment is outside the 30-day refund window. You can cancel renewal or request human review.",
    "REFUND_ALREADY_REQUESTED": "A refund has already been requested for this payment. Check its current status below.",
    "REFUND_CAP_REACHED": "This refund would exceed the account's 30-day limit. Please request human review.",
}


class WorkflowService:
    # Dependency injection supplies the database-backed business service and one
    # extractor. model_timeout is a deadline in seconds for extraction only.
    def __init__(self, business: BusinessService, extractor: Extractor, sessions: SessionService | None = None,
                 model_timeout: float = 20):
        self.business = business
        self.sessions = sessions or SessionService(business)
        self.extractor = extractor
        self.model_timeout = model_timeout

    async def chat(self, session: CustomerSession, message: str, conv_id: str, request_id: str) -> dict:
        # to_thread runs blocking SQLite work in a worker thread so async HTTP handlers
        # can keep servicing other connections. Each business call opens its own connection.
        started = time.perf_counter()
        state = await asyncio.to_thread(self.sessions.begin, session, conv_id, request_id, message)
        if "response" in state:
            # A completed retry returns immediately: no new extraction or business write.
            return state["response"]
        try:
            overview = await asyncio.to_thread(self.business.overview, session.scope)
            if state["plan"]:
                # Recovery uses the first persisted plan even if the model would answer
                # differently now. Structural validation still runs when loading saved data.
                plan = ExtractedPlan.model_validate(state["plan"])
            else:
                try:
                    async with asyncio.timeout(self.model_timeout):
                        plan = await self.extractor.extract(message, state["recent"], overview)
                    plan = ExtractedPlan.model_validate(plan)
                except Exception:
                    # A timeout, invalid JSON, or invalid plan becomes clarification.
                    # Do not log prompt contents or turn failure into a default refund.
                    logger.warning("Business intent extraction failed; requesting clarification", exc_info=False)
                    plan = ExtractedPlan(intent="clarify")
                # model_dump converts the validated object to a plain serializable dict.
                # Await persistence before dispatching any operation with side effects.
                saved = await asyncio.to_thread(self.sessions.save_plan, session, request_id, state["claim"], plan.model_dump())
                plan = ExtractedPlan.model_validate(saved)
            text, action, handoff = await asyncio.to_thread(self._dispatch, session, request_id, message, plan, overview)
            actions = await asyncio.to_thread(self.business.list_actions, session.scope, conv_id)
            # All displayed statuses come from persisted actions; latency is measured in
            # milliseconds. The response is saved so a network retry can receive it unchanged.
            response = {"conv_id": conv_id, "response": text, "mode": "business_workflow", "agent_type": "business",
                        "intent": plan.intent, "escalated": handoff is not None, "knowledge_used": False,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                        "actions": actions, "handoff": handoff,
                        "operation_id": action["id"] if action else None}
            return await asyncio.to_thread(self.sessions.complete, session, request_id, state["claim"], response)
        except BaseException:
            # shield helps finish lease release if this coroutine is cancelled. It cannot
            # guarantee cleanup after process termination; the finite lease handles that case.
            # A cancelled thread may still commit. Retrying the same request reuses the saved plan and operation ID.
            await asyncio.shield(asyncio.to_thread(self.sessions.release, session, state["claim"]))
            raise

    def _dispatch(self, session, request_id, message, plan, overview):
        # Return (reply text, optional action, optional handoff ticket). This explicit
        # branch table executes at most one requested business action per message.
        scope, conv_id = session.scope, session.conv_id
        # The application builds this identity, never the model. Repeating the same
        # message after a crash reaches the same domain operation and cannot refund twice.
        operation_id = f"{conv_id}:{request_id}"
        if plan.intent == "charges":
            # Divide integer cents only for presentation; business calculations stay integers.
            lines = [f"{c['id']}: {c['amount_cents'] / 100:.2f} {c['currency']}" + (" (refunded)" if c["refunded"] else "")
                     for c in overview["charges"]]
            return "Your payments:\n" + "\n".join(lines), None, None
        if plan.intent == "status":
            # Query fresh state rather than treating an earlier assistant reply as truth.
            actions = self.business.list_actions(scope, conv_id)
            text = "\n".join(f"{a['summary']}: {a['status']} ({a['reason_code']})." for a in actions)
            return text or "There are no operations in this conversation yet.", None, None
        if plan.intent == "handoff":
            # A persisted case is not a live connection to staff; the reply says so.
            ticket = self.business.handoff(scope, conv_id, message)
            return f"Your case has been recorded for human review. Ticket {ticket['id']}. A live agent is not connected yet.", None, ticket
        if plan.intent == "renewal":
            # None differs from False: missing direction must be clarified, while False
            # explicitly asks to restore renewal.
            if plan.cancel_at_period_end is None:
                return "Would you like to cancel renewal or restore it?", None, None
            action = self.business.execute(scope, conv_id, operation_id, "renewal", {"cancel_at_period_end": plan.cancel_at_period_end})
            text = "Your membership will not renew at the end of this period." if plan.cancel_at_period_end else "Your membership renewal is enabled."
            return text, action, None
        if plan.intent == "refund":
            # Resolve an omitted charge ID only if account facts identify one candidate.
            # Duplicate pairing uses the same order, amount, currency, and <=900s gap;
            # the domain service repeats the check inside its transaction before writing.
            charge_id = plan.charge_id
            if not charge_id and plan.reason_code == "duplicate_charge":
                candidates = [c for c in overview["charges"] if any(
                    old["order_id"] == c["order_id"] and old["amount_cents"] == c["amount_cents"]
                    and old["currency"] == c["currency"] and 0 < c["created_at"] - old["created_at"] <= 900
                    for old in overview["charges"])]
                charge_id = candidates[0]["id"] if len(candidates) == 1 else None
            if not charge_id and len(overview["charges"]) == 1:
                charge_id = overview["charges"][0]["id"]
            if not charge_id:
                # Ambiguity ends this turn with a question; no write occurs.
                return "Which payment should I refund? Ask to list your payments, then provide its payment ID.", None, None
            try:
                action = self.business.execute(scope, conv_id, operation_id, "refund",
                                               {"charge_id": charge_id, "reason_code": plan.reason_code})
            except BusinessError as error:
                if error.code == "NOT_FOUND":
                    return "That payment was not found in your demo account. Please check its ID.", None, None
                raise
            if action["status"] == "executed":
                # Only executed may say completed. pending must explicitly say not refunded.
                text = f"Your refund of {action['amount_cents'] / 100:.2f} EUR is completed in this simulated account."
            elif action["status"] == "pending":
                text = "Your refund request has been submitted for supervisor review. It has not been refunded yet."
            else:
                text = DENIALS.get(action["reason_code"], f"This request could not be completed: {action['reason_code']}.")
            return text, action, None
        # clarify and any remaining safe path describe supported choices without executing.
        return "I can check payments, request a refund, cancel or restore renewal, check an operation, or record a human handoff. Please choose one.", None, None
