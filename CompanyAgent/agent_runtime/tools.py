"""Typed tool gateway: trusted identity, exact write grants and stable operation IDs.

The model may choose tools and their order. It cannot choose an account, approve an
action, or broaden the explicit customer grant. BusinessService remains the final
authority for thresholds, quotas and side effects. Read-only tools still need scope.
"""
import asyncio
import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_runtime.context import SKILLS, VERSION, search
from business.models import BusinessError, Scope


class Arguments(BaseModel):
    """Reject extra model fields, including invented customer or approval IDs."""
    model_config = ConfigDict(extra="forbid", strict=True)


class Empty(Arguments):
    """Tools without inputs accept exactly an empty object."""


class Refund(Arguments):
    charge_id: str = Field(min_length=1, max_length=80)
    reason_code: str = Field(pattern="^(duplicate_charge|other)$")


class Renewal(Arguments):
    cancel_at_period_end: bool


class Query(Arguments):
    query: str = Field(min_length=1, max_length=500)


class Skill(Arguments):
    name: str = Field(pattern="^(diagnosis|billing|approval)$")


class Incident(Arguments):
    incident_id: str = Field(min_length=1, max_length=80)


class Handoff(Arguments):
    summary: str = Field(min_length=1, max_length=1000)


# Names and descriptions form the model-facing contract. The Python validators
# enforce it again; a provider's JSON grammar is not an authorization mechanism.
DEFINITIONS = {
    "get_account": (Empty, "Read this authenticated customer's payments and subscription."),
    "get_actions": (Empty, "Read this conversation's saved action statuses."),
    "request_refund": (Refund, "Request a FULL refund for the exact payment in the explicit customer grant."),
    "set_renewal": (Renewal, "Change renewal only to the value in the explicit customer grant."),
    "create_handoff": (Handoff, "Save one human-support ticket; this does not connect a live employee."),
    "search_knowledge": (Query, "Search trusted repository runbooks and return cited evidence."),
    "load_skill": (Skill, "Load one versioned procedure: diagnosis, billing, or approval."),
    "get_service_status": (Empty, "Read the synthetic service status; degraded status includes an incident ID."),
    "get_incident": (Incident, "Read the known incident details after the status tool identifies it."),
}


def schemas():
    # Reading note: For each registry entry build a fresh dictionary containing name,
    # description and Pydantic JSON schema. The list comprehension iterates in registry order.
    # These descriptions help model selection; the gateway validates again because a schema is
    # not an authorization boundary.
    """Return fresh schemas so provider-specific edits cannot mutate the registry."""
    return [{"name": name, "description": desc, "input_schema": cls.model_json_schema()}
            for name, (cls, desc) in DEFINITIONS.items()]


def diagnostic(name, args, status="degraded", incident="incident-demo-1"):
    # Reading note: name selects one of two fixture reads; args contains the incident ID for
    # the second. status and incident are synthetic test/environment values. Return a
    # dictionary with an evidence ID and synthetic=true, or raise for an unknown incident. No
    # external monitoring or customer mutation occurs.
    """Pure synthetic observations shared by local and real MCP transports."""
    if name == "get_service_status":
        return {"status": status, "incident_id": incident if status == "degraded" else None,
                "evidence_id": "service:status", "synthetic": True}
    if args["incident_id"] != incident:
        raise ValueError("Unknown incident")
    return {"incident_id": incident, "cause": "A synthetic gateway deployment increased login errors.",
            "status": "investigating", "evidence_id": "incident:demo-1", "synthetic": True}


class Gateway:
    """Execute one validated call with run ownership injected by the server.

    mcp may supply only diagnostic reads. knowledge may be an async existing RAG
    adapter; without it the versioned repository runbooks remain a usable fallback.
    """

    def __init__(self, business, store, mcp=None, knowledge=None):
        # Reading note: business enforces existing financial policy, store journals calls,
        # optional mcp supplies remote diagnostic reads and optional knowledge is an async
        # retrieval function. Dependency injection lets offline tests replace transports while
        # keeping the same permission checks. None selects the bounded local fallback, not a
        # hidden paid model.
        self.business, self.store, self.mcp, self.knowledge = business, store, mcp, knowledge

    async def execute(self, row, call, owner, version):
        # Reading note: row is a trusted saved run, call contains native id/name/input, and
        # owner/version are this worker fencing credentials. Journal first for replay,
        # validate strict arguments and inject server scope. A write additionally matches its
        # exact customer grant and one-write-per-input slot before BusinessService runs under
        # a stable operation ID. Reads return bounded evidence; known policy/transport
        # failures become structured errors. Unexpected exceptions stop the engine, and
        # cancellation never authorizes another call.
        """Return structured data/errors; never execute Python or SQL from arguments."""
        run, name, args = row["id"], call["name"], call.get("input", {})
        key = "tool:" + call["id"]
        previous = self.store.journal(run, key, "tool", {"name": name, "args": args}, owner, version)
        if previous is not None:
            return previous
        try:
            if name not in DEFINITIONS:
                raise BusinessError("NOT_ALLOWED", "Unknown tool", 422)
            args = DEFINITIONS[name][0].model_validate(args).model_dump()
            scope = Scope(row["sandbox"], row["customer"])
            payload = json.loads(row["payload"])
            grant = payload.get("grant", {})
            if name in {"request_refund", "set_renewal"}:
                expected = "refund" if name == "request_refund" else "renewal"
                if grant.get("kind") != expected:
                    raise BusinessError("CONSENT_REQUIRED", "An explicit customer write grant is required", 403)
                target = "charge_id" if expected == "refund" else "cancel_at_period_end"
                if args[target] != grant.get(target):
                    raise BusinessError("CONSENT_REQUIRED", "Tool arguments differ from the customer grant", 403)
                # At most one new write per user input. Replaying this same call is
                # allowed; another ID in the same input generation is not.
                generation = payload.get("input_generation", 0)
                with self.store.db.transaction() as conn:
                    self.store.guard(conn, run, owner, version)
                    changed = conn.execute("SELECT id FROM agent_calls WHERE run=? AND kind=?", (run, f"write:{generation}")).fetchone()
                    if changed and changed[0] != key:
                        raise BusinessError("WRITE_LIMIT", "Only one write per customer input", 429)
                    conn.execute("UPDATE agent_calls SET kind=? WHERE run=? AND id=?", (f"write:{generation}", run, key))
                operation = f"agent:{run}:{call['id']}"
                # Each operation key is under the existing 128-character bound.
                action = await asyncio.to_thread(self.business.execute, scope, row["conv"], operation, expected, args)
                result = {"action": action, "evidence_id": "action:" + action["id"]}
            elif name == "get_account":
                result = {**await asyncio.to_thread(self.business.overview, scope), "evidence_id": "account:current"}
            elif name == "get_actions":
                result = {"items": await asyncio.to_thread(self.business.list_actions, scope, row["conv"]), "evidence_id": "actions:current"}
            elif name == "create_handoff":
                result = {"ticket": await asyncio.to_thread(self.business.handoff, scope, row["conv"], args["summary"]),
                          "evidence_id": "handoff:current"}
            elif name == "load_skill":
                result = {"name": args["name"], "version": VERSION, "instructions": SKILLS[args["name"]],
                          "evidence_id": "skill:" + args["name"]}
            elif name == "search_knowledge":
                result = {"items": search(args["query"])}
                if self.knowledge:
                    # Existing RAG is optional and its output remains untrusted data.
                    # A retriever failure does not erase repository evidence.
                    try:
                        result["additional"] = await asyncio.wait_for(self.knowledge(args["query"]), 10)
                    except Exception:
                        result["retrieval_unavailable"] = True
            elif self.mcp:
                result = await asyncio.wait_for(self.mcp.call(name, args), 10)
            else:
                result = diagnostic(name, args, payload.get("diagnostic", "degraded"), payload.get("incident", "incident-demo-1"))
            # Bound a result before putting it in model context. The gateway itself
            # creates all public keys; external content cannot overwrite policy data.
            encoded = json.dumps(result, ensure_ascii=False)
            if len(encoded) > 12000:
                result = {"error": "OUTPUT_TOO_LARGE", "message": "Narrow the query"}
        except (BusinessError, ValidationError, ValueError) as error:
            result = {"error": getattr(error, "code", "INVALID_ARGUMENTS"), "message": "Tool request was not permitted or valid"}
        except (TimeoutError, ConnectionError):
            result = {"error": "TOOL_UNAVAILABLE", "message": "Read service unavailable; do not claim a result"}
        self.store.complete_call(run, key, result)
        return result
