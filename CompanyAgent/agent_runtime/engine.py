"""Small LangGraph execution loop with durable tool/model journals and interrupts.

The graph owns control flow, the store owns fencing/quotas, and BusinessService owns
financial facts. A checkpoint is not a distributed transaction: every node journals
external work first so re-entering the node can reuse the original result.
"""
import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent_runtime.context import VERSION, project_messages
from agent_runtime.tools import schemas
from business.models import BusinessError, Scope
from observability.telemetry import tracer


class RunState(TypedDict, total=False):
    """JSON-only checkpoint values; credentials and live clients never enter state."""
    run: str
    messages: list
    observations: list
    step: int
    tool_count: int
    no_progress: int
    status: str
    result: dict
    pending: str
    content: list
    context_stats: dict
    schema_version: str


def evidence_ids(value):
    # Reading note: value may be a dictionary, list or scalar. Recursion visits nested
    # containers and unions their evidence IDs into a set (which removes duplicates).
    # Empty/scalar values contribute an empty set. These IDs prove that a source was observed,
    # not that every sentence in a final answer is true.
    """Collect only IDs actually returned in structured observations."""
    if isinstance(value, dict):
        return {value["evidence_id"]} | set().union(*(evidence_ids(v) for v in value.values())) if "evidence_id" in value else set().union(*(evidence_ids(v) for v in value.values()), set())
    if isinstance(value, list):
        return set().union(*(evidence_ids(v) for v in value), set())
    return set()


class AgentEngine:
    """Single-process worker runtime; create once inside application lifespan.

    checkpoint_path stores graph state on disk; model/gateway/store are injected so
    tests need neither the provider nor Redis/Chroma. start/close own all resources.
    """

    def __init__(self, store, gateway, model, checkpoint_path):
        # Reading note: store, gateway and model are ready-made dependencies passed by the
        # caller; assigning them to self makes later methods share them. checkpoint_path is a
        # local SQLite file path. owner is a random worker identity, claims maps run IDs to
        # fencing versions, and background starts empty. No worker or paid request starts in
        # this constructor.
        self.store, self.gateway, self.model = store, gateway, model
        self.path = str(checkpoint_path)
        self.owner = str(uuid.uuid4())
        self.claims = {}
        self.background = None
        self.closed = False

    async def start(self, worker=False):
        # Reading note: worker defaults to false for manually driven tests or read-only
        # rollback. async with ownership is managed explicitly: open the SQLite saver,
        # construct named graph nodes and conditional edges, then compile with that saver.
        # Return self so startup can await construction in one expression. Only worker=true
        # creates a background queue scanner; close must later release the saver.
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.saver_context = AsyncSqliteSaver.from_conn_string(self.path)
        saver = await self.saver_context.__aenter__()
        graph = StateGraph(RunState)
        graph.add_node("model", self._model)
        graph.add_node("tools", self._tools)
        graph.add_node("finish", self._finish)
        graph.add_node("wait", self._wait)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", lambda s: "tools" if any(b.get("type") == "tool_use" for b in s.get("content", [])) else "finish")
        graph.add_conditional_edges("tools", lambda s: "wait" if s.get("pending") else "model")
        graph.add_conditional_edges("finish", lambda s: "wait" if s["status"] == "waiting_user" else END)
        graph.add_edge("wait", "model")
        self.graph = graph.compile(checkpointer=saver)
        if worker:
            # The journal is the queue. This task only wakes it; lost in-memory task
            # handles do not lose runs. Startup scans the same persistent records.
            self.background = asyncio.create_task(self._worker())
        return self

    async def close(self):
        # Reading note: Set closed first so the scanner cannot schedule more runs. If its
        # asyncio task exists, cancel and await it: awaiting allows the active drive call to
        # save its shutdown state. CancelledError here is expected cleanup, not a successful
        # business operation. Finally exit the saver context to close its database connection;
        # this never deletes persisted files.
        self.closed = True
        if self.background:
            self.background.cancel()
            try:
                await self.background
            except asyncio.CancelledError:
                pass
        await self.saver_context.__aexit__(None, None, None)

    def _identity(self, state):
        # Reading note: state contains the graph run ID. Look up the worker identity/version
        # acquired by drive, fetch the current run row, and ask the store to check lease,
        # cancellation and session expiry inside a transaction. Return row, owner and version
        # for the next node. A stale worker raises instead of starting another external call.
        owner, version = self.claims[state["run"]]
        row = self.store.get(state["run"])
        with self.store.db.transaction() as conn:
            self.store.guard(conn, state["run"], owner, version)
        return row, owner, version

    async def _model(self, state):
        # Reading note: state carries prior messages and integer step/tool counters. Enforce
        # bounds first, obtain the current exact grant from the run, and project old
        # observations. final_only disables tools after repeated evidence or near the cap.
        # Reserve integer microdollars BEFORE inference and journal the numbered call; a saved
        # reply is reused. Await a new response only when no reply exists, settle valid usage,
        # reject truncation, then return the state fields that LangGraph must update.
        row, owner, version = self._identity(state)
        if state["step"] >= 8 or state["no_progress"] >= 3:
            raise BusinessError("STEP_LIMIT", "Stopped without new useful evidence")
        grant = json.loads(row["payload"]).get("grant", {})
        projected, stats = project_messages(state["messages"])
        if len(json.dumps(projected)) > 60000:
            raise BusinessError("CONTEXT_LIMIT", "Conversation exceeds safe context limit")
        final_only = state["no_progress"] >= 1 or state["step"] >= 7 or state["tool_count"] >= 10
        held = self.model.reservation(projected, schemas(), grant, final_only)
        key = f"model:{state['step']}"
        reply = self.store.journal(row["id"], key, "model", {"name": "agent.turn", "messages": projected,
                                   "grant": grant, "schema": VERSION, "final_only": final_only}, owner, version, held)
        if reply is None:
            async with asyncio.timeout(30):
                reply = await self.model.turn(projected, schemas(), grant, final_only)
            self.store.complete_call(row["id"], key, reply, reply["cost_micro"])
        if reply["stop_reason"] not in {"end_turn", "tool_use"}:
            raise BusinessError("MODEL_INCOMPLETE", "Model response was truncated or refused")
        content = reply["content"]
        return {"messages": state["messages"] + [{"role": "assistant", "content": content}],
                "content": content, "step": state["step"] + 1, "context_stats": stats}

    async def _tools(self, state):
        # Reading note: Read only native tool_use blocks from the current response. Reject
        # repeated/oversized call IDs and a batch that exceeds twelve total tools. At most two
        # read-only calls run together with gather; writes and larger batches run in order.
        # Each result is paired to its original call ID in a tool_result block. New evidence
        # resets no_progress; pending actions route to an interrupt. Returning a dictionary
        # updates graph state rather than mutating the user account directly.
        row, owner, version = self._identity(state)
        calls = [b for b in state["content"] if b.get("type") == "tool_use"]
        if state["tool_count"] + len(calls) > 12:
            raise BusinessError("TOOL_LIMIT", "Tool call limit reached")
        ids = [c.get("id", "") for c in calls]
        if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not 0 < len(i) <= 70 for i in ids):
            raise BusinessError("INVALID_CALL_ID", "Duplicate or invalid tool call identity")
        results = []
        # A maximum of two independent reads share a batch. Writes are serialized
        # with all other operations so one observation cannot race its own mutation.
        read_names = {"get_account", "get_actions", "get_service_status", "get_incident", "search_knowledge", "load_skill"}
        if len(calls) <= 2 and all(c["name"] in read_names for c in calls):
            results = await asyncio.gather(*(self.gateway.execute(row, c, owner, version) for c in calls))
        else:
            for call in calls:
                results.append(await self.gateway.execute(row, call, owner, version))
        observations = state["observations"] + results
        pending = next((r["action"]["id"] for r in results if r.get("action", {}).get("status") == "pending"), "")
        known = evidence_ids(state["observations"])
        progress = bool(evidence_ids(results) - known)
        blocks = [{"type": "tool_result", "tool_use_id": c["id"], "content": json.dumps(r, ensure_ascii=False),
                   "is_error": "error" in r} for c, r in zip(calls, results)]
        return {"messages": state["messages"] + [{"role": "user", "content": blocks}],
                "observations": observations, "tool_count": state["tool_count"] + len(calls),
                "no_progress": 0 if progress else state["no_progress"] + 1, "pending": pending,
                "status": "waiting_approval" if pending else "running"}

    async def _finish(self, state):
        # Reading note: Join final text blocks and require the small JSON contract and
        # observed source IDs. Malformed output raises INVALID_FINAL without a repair-model
        # call. Read fresh business actions using the server-owned scope; limited phrase
        # checks suppress some unsupported refund-completion claims, while the separate fact
        # cards remain authoritative. This is structural/fact checking, not a full semantic
        # proof. Return completed or waiting_user from the declared outcome.
        row, _, _ = self._identity(state)
        text = "".join(b.get("text", "") for b in state["content"] if b.get("type") == "text").strip()
        # The final schema is small and strict. Never extract a convenient object
        # from surrounding prose or treat a malformed model answer as a business fact.
        if text.startswith("```json\n") and text.endswith("\n```"):
            text = text[8:-4]
        try:
            final = json.loads(text)
            if (set(final) != {"response", "outcome", "evidence_ids"} or not isinstance(final["response"], str)
                    or final["outcome"] not in {"resolved", "needs_input", "handoff"}
                    or not isinstance(final["evidence_ids"], list)
                    or not all(isinstance(x, str) for x in final["evidence_ids"])):
                raise ValueError()
            if not set(final["evidence_ids"]).issubset(evidence_ids(state["observations"])):
                raise ValueError("Invented evidence")
        except (ValueError, TypeError):
            raise BusinessError("INVALID_FINAL", "Model final answer lacks verifiable structure") from None
        if final["outcome"] == "handoff" and not any("ticket" in o for o in state["observations"]):
            raise BusinessError("UNVERIFIED_HANDOFF", "No recorded handoff supports this answer")
        actions = await asyncio.to_thread(self.gateway.business.list_actions, Scope(row["sandbox"], row["customer"]), row["conv"])
        # An explanation cannot turn a pending/denied request into a completed refund.
        # The UI always displays these database facts separately from model language.
        lower = final["response"].lower()
        observed_actions = {o["action"]["id"] for o in state["observations"] if "action" in o}
        if any(w in lower for w in ["refunded", "money returned", "refund completed", "refund processed"]):
            if not any(a["id"] in observed_actions and a["tool"] == "refund" and a["status"] == "executed" for a in actions):
                final["response"] = "No completed refund is recorded. Please check the action status shown below."
        final["actions"] = actions
        final["context"] = state["context_stats"]
        return {"result": final, "status": "waiting_user" if final["outcome"] == "needs_input" else "completed"}

    async def _wait(self, state):
        # interrupt re-enters this node on resume. It performs no write before the
        # interrupt; the caller supplies only a server-validated continuation event.
        # Reading note: interrupt saves a pause and stops graph execution. On a verified
        # continuation LangGraph re-enters this node and returns its resume value. Append that
        # value as a user-role observation, clear the pending ID and reset no-progress. There
        # is no financial write before the interrupt, because this function may execute again
        # during recovery.
        value = interrupt({"kind": state["status"], "action_id": state.get("pending", "")})
        return {"messages": state["messages"] + [{"role": "user", "content": value}],
                "pending": "", "status": "running", "no_progress": 0}

    async def drive(self, run):
        # Reading note: run is a saved identifier, not a model-chosen account. First reconcile
        # an actual staff action, then claim a lease/version; another owner means return the
        # current public status. Load the graph snapshot, reject incompatible versions, and
        # resume an interrupt or start/re-enter the graph. A slice lasts at most 90 seconds
        # and total active time 180 seconds; human waiting is excluded. Exceptions persist a
        # safe stopped status, shutdown preserves journals, and finally records elapsed active
        # seconds and clears the in-memory claim.
        """Claim and execute one bounded slice, including recovery of interrupted nodes."""
        row = self.store.get(run)
        if row["status"] == "waiting_approval":
            action = next((a for a in self.gateway.business.list_actions(Scope(row["sandbox"], row["customer"]), row["conv"])
                           if a["id"] == json.loads(row["result"]).get("action_id")), None)
            if not action or action["status"] == "pending":
                return self.store.public(run)
            # The authenticated business ledger, never customer JSON, wakes approval.
            with self.store.db.transaction() as conn:
                conn.execute("UPDATE agent_runs SET status='queued',payload=json_set(payload,'$.approval_observation',?) "
                             "WHERE id=? AND status='waiting_approval'", (json.dumps(action), run))
        version = self.store.claim(run, self.owner)
        if version is None:
            return self.store.public(run)
        self.claims[run] = (self.owner, version)
        config = {"configurable": {"thread_id": run}, "recursion_limit": 40}
        row = self.store.get(run)
        payload = json.loads(row["payload"])
        started = time.monotonic()
        try:
            snapshot = await self.graph.aget_state(config)
            if snapshot.values and snapshot.values.get("schema_version") != VERSION:
                raise BusinessError("STATE_VERSION", "Run requires its original runtime version")
            if snapshot.tasks and any(t.interrupts for t in snapshot.tasks):
                value = payload.get("resume_input")
                if "approval_observation" in payload:
                    value = "Verified staff action status: " + payload["approval_observation"]
                if not value:
                    raise BusinessError("STALE_RUN", "Missing authenticated continuation")
                initial = Command(resume=value)
            elif snapshot.values:
                initial = None
            else:
                initial = {"run": run, "messages": [{"role": "user", "content": row["message"]}], "observations": [],
                           "step": 0, "tool_count": 0, "no_progress": 0, "pending": "", "status": "running",
                           "result": {}, "schema_version": VERSION}
            if payload.get("active_seconds", 0) >= 180:
                raise BusinessError("TIME_LIMIT", "Cumulative active deadline reached")
            with tracer.start_as_current_span("agent.run") as span:
                span.set_attribute("agent.run.id", run)
                async with asyncio.timeout(min(90, 180 - payload.get("active_seconds", 0))):
                    await self.graph.ainvoke(initial, config)
            values = (await self.graph.aget_state(config)).values
            status = values["status"]
            result = values.get("result", {})
            if status == "waiting_approval":
                result = {"action_id": values["pending"], "response": "Waiting for authenticated staff review.",
                          "actions": self.gateway.business.list_actions(Scope(row["sandbox"], row["customer"]), row["conv"])}
            self.store.update(run, self.owner, version, status, result)
        except asyncio.CancelledError:
            # A shutdown preserves journals. Expiring this owner's lease lets startup
            # recover persisted work without resetting either a call ID or its budget.
            with self.store.db.transaction() as conn:
                conn.execute("UPDATE agent_runs SET lease_until=0 WHERE id=? AND lease_owner=? AND version=?",
                             (run, self.owner, version))
            raise
        except Exception as error:
            code = getattr(error, "code", type(error).__name__)
            status = "budget_exhausted" if code in {"BUDGET_EXHAUSTED", "BudgetStop"} else "failed"
            if code == "CANCELLED":
                status = "cancelled"
            self.store.update(run, self.owner, version, status, {"error": code, "response": "Run stopped. Existing actions remain recorded."})
        finally:
            # Add elapsed *active* seconds; waiting for a person consumes no runtime.
            with self.store.db.transaction() as conn:
                conn.execute("UPDATE agent_runs SET payload=json_set(payload,'$.active_seconds',"
                             "COALESCE(json_extract(payload,'$.active_seconds'),0)+?) WHERE id=?",
                             (time.monotonic() - started, run))
            self.claims.pop(run, None)
        return self.store.public(run)

    async def _worker(self):
        # Reading note: The loop scans persisted candidate IDs and awaits one bounded drive at
        # a time. Its 0.4-second sleep only limits polling traffic; the database lease, not
        # the timer, prevents two owners. close sets the flag and cancels this task. Waiting
        # approvals are inspected for real staff decisions without purchasing repeated model
        # responses.
        while not self.closed:
            for run in self.store.candidates():
                if self.closed:
                    return
                await self.drive(run)
            # This is scheduling, not a correctness mechanism. The persistent queue
            # and lease checks remain authoritative across wakeups and process death.
            await asyncio.sleep(0.4)
