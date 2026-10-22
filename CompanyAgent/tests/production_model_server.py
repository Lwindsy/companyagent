"""Synthetic Messages API for isolated production-image acceptance, never deployment.

The real api.main lifespan, SDK, router and SQLite execute normally. Only the paid
model is replaced by this exact-phrase HTTP service. It never reads credentials,
opens a customer database, or claims to measure natural-language understanding.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json


# These phrases match the synthetic accounts and acceptance steps. Unrecognized
# business wording clarifies instead of guessing a write. No answer-key file is read.
PLANS = {
    "I was charged twice. Please refund the duplicate payment.": {"intent": "refund", "reason_code": "duplicate_charge"},
    "Please refund my recent 450 EUR payment.": {"intent": "refund"},
    "Please refund my membership renewal payment.": {"intent": "refund"},
    "Cancel renewal.": {"intent": "renewal", "cancel_at_period_end": True},
    "Restore renewal.": {"intent": "renewal", "cancel_at_period_end": False},
    "Check my request status.": {"intent": "status"},
    "Please connect me to a human.": {"intent": "handoff"},
}


class Handler(BaseHTTPRequestHandler):
    """Accept local SDK-shaped messages and return a synthetic text-only response."""

    def do_POST(self):
        # Docker's private test network is the boundary; reject unrelated endpoints
        # and overlarge bodies rather than serving a general model proxy.
        size = int(self.headers.get("Content-Length", "0"))
        if self.path != "/v1/messages" or not 0 < size <= 100_000:
            self.send_error(400)
            return
        body = json.loads(self.rfile.read(size))
        content = body["messages"][-1]["content"]
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            payload = {}
        if body.get("tools") and "case owner" in body.get("system", ""):
            # This explicit reference controller exercises native tool blocks through
            # the actual SDK and graph. It is not a natural-language quality model.
            from evaluation.agent_tasks.reference import reference
            grant_text = body["system"].split("CURRENT SERVER GRANT: ")[-1]
            planned = reference(body["messages"], body["tools"], json.loads(grant_text))
            response = {"id": "msg-agent-acceptance", "type": "message", "role": "assistant",
                        "model": body["model"], "stop_reason": planned["stop_reason"], "stop_sequence": None,
                        "content": planned["content"], "usage": {"input_tokens": 100, "output_tokens": 30}}
        elif "current_customer_message" in payload:
            text = json.dumps(PLANS.get(payload["current_customer_message"], {"intent": "clarify"}))
        else:
            # General-support compatibility can also reach this local replacement.
            # It is a fixed answer, not a fabricated real-model quality measurement.
            text = "This is an isolated general-support acceptance reply."
        if not (body.get("tools") and "case owner" in body.get("system", "")):
            response = {"id": "msg-acceptance", "type": "message", "role": "assistant",
                        "model": body["model"], "stop_reason": "end_turn", "stop_sequence": None,
                        "content": [{"type": "text", "text": text}],
                        "usage": {"input_tokens": 100, "output_tokens": 30}}
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_):
        # Suppress request logs entirely; no Authorization header should be recorded.
        return


if __name__ == "__main__":
    # 0.0.0.0 is reachable only within the dedicated Docker network: this service
    # has no published port and is never mounted by the deployment configuration.
    ThreadingHTTPServer(("0.0.0.0", 8991), Handler).serve_forever()
