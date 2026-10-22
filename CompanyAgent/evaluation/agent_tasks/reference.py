"""Deterministic protocol controller for offline integration and reference solutions.

This is a test oracle, not an LLM, an architectural benchmark score, or production
decision logic. It receives normal messages/observations only, never task labels.
"""
import json


def observations(messages):
    """Decode completed native tool observations from the conversation."""
    return [json.loads(b["content"]) for m in messages if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"] if b.get("type") == "tool_result"]


def reference(messages, tools, grant):
    """Exercise tool rounds from evidence; hidden expected states never enter here."""
    seen = observations(messages)
    text = " ".join(m["content"] for m in messages if m["role"] == "user" and isinstance(m["content"], str)).lower()
    names = [b["name"] for m in messages if m["role"] == "assistant" and isinstance(m["content"], list)
             for b in m["content"] if b.get("type") == "tool_use"]

    def call(name, args=None):
        return {"content": [{"type": "tool_use", "name": name, "input": args or {}, "id": f"ref-{len(names)}"}],
                "stop_reason": "tool_use", "cost_micro": 0}

    def final(response="Checked the recorded facts.", outcome="resolved"):
        return {"content": [{"type": "text", "text": json.dumps({"response": response, "outcome": outcome,
                "evidence_ids": []})}], "stop_reason": "end_turn", "cost_micro": 0}

    if "partial" in text or "only 10" in text:
        return final("Only full-payment refunds are supported. Which supported action do you want?", "needs_input")
    if "human" in text or "person" in text:
        return call("create_handoff", {"summary": "Customer requests human support"}) if not seen else final("A handoff ticket was saved.", "handoff")
    if "service" in text or "401" in text or "login" in text:
        if "get_service_status" not in names:
            return call("get_service_status")
        status = next((x for x in seen if "incident_id" in x), {})
        if status.get("status") == "degraded" and "get_incident" not in names:
            return call("get_incident", {"incident_id": status["incident_id"]})
        if status.get("status") == "healthy" and "search_knowledge" not in names:
            return call("search_knowledge", {"query": "login 401"})
        return final("I checked the service observation and its supporting evidence.")
    if "get_account" not in names:
        return call("get_account")
    if grant.get("kind") == "refund" and "request_refund" not in names:
        return call("request_refund", {"charge_id": grant["charge_id"], "reason_code": "duplicate_charge" if "duplicate" in text else "other"})
    if grant.get("kind") == "renewal" and "set_renewal" not in names:
        return call("set_renewal", {"cancel_at_period_end": grant["cancel_at_period_end"]})
    return final()
