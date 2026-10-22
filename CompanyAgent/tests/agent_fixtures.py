"""Reusable offline runtime fixtures and native protocol responses, without dotenv.

Every test owns its SQLite directory and synthetic account. These helpers never
authenticate to a real provider; scripted replies are explicitly mechanical tests.
"""
import json

from agent_runtime.engine import AgentEngine
from agent_runtime.model import ScriptedModel
from agent_runtime.store import RunStore
from agent_runtime.tools import Gateway
from business.db import Database
from business.service import BusinessService
from workflows.sessions import SessionService


def call(name, arguments=None, identity="call-1"):
    """Construct the same block shape the native provider returns for a tool request."""
    return {"content": [{"type": "tool_use", "id": identity, "name": name, "input": arguments or {}}],
            "stop_reason": "tool_use", "cost_micro": 0}


def finish(text="Verified.", evidence=None, outcome="resolved"):
    """Build a structured final reply; evidence must name actual observed IDs."""
    return {"content": [{"type": "text", "text": json.dumps({"response": text, "outcome": outcome,
                        "evidence_ids": evidence or []})}], "stop_reason": "end_turn", "cost_micro": 0}


async def runtime(folder, decide, persona="p_duplicate", clock=None):
    """Return (engine, session, sessions) using real policies and an offline model."""
    db = Database(str(folder / "business.db"))
    db.initialize()
    business = BusinessService(db, **({"clock": clock} if clock else {}))
    sessions = SessionService(business, sessions_per_ip_hour=100)
    created = sessions.create(persona, "offline-fixture")
    session = sessions.authenticate(created["token"])
    store = RunStore(db, **({"clock": clock} if clock else {}))
    engine = await AgentEngine(store, Gateway(business, store), ScriptedModel(decide), folder / "graph.db").start()
    return engine, session, sessions


def observations(messages):
    """Read only tool observations from a normal conversation, never hidden labels."""
    result = []
    for message in messages:
        if message["role"] == "user" and isinstance(message["content"], list):
            result.extend(json.loads(b["content"]) for b in message["content"] if b.get("type") == "tool_result")
    return result
