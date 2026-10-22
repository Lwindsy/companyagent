"""Persistent identity and request replay. Does not share anonymous Redis/Chroma memory."""
import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass

from business.models import BusinessError, Scope
from business.service import BusinessService, fingerprint


@dataclass(frozen=True)
class CustomerSession:
    # Authentication produces this trusted value object. Do not construct its scope
    # from a customer message. token_hash is a lookup digest, not the raw bearer token.
    token_hash: str
    scope: Scope
    conv_id: str


class SessionService:
    # ttl_seconds limits token life; max_messages limits first-time customer requests.
    # Other limits bound demo creation, not exact dollar spending or production identity.
    def __init__(self, business: BusinessService, ttl_seconds: int = 7200, max_messages: int = 40,
                 sessions_per_ip_hour: int = 10, max_sandboxes: int = 200):
        self.business = business
        self.db = business.db
        self.ttl = ttl_seconds
        self.max_messages = max_messages
        self.sessions_per_ip_hour = sessions_per_ip_hour
        self.max_sandboxes = max_sandboxes

    def create(self, persona_id: str, peer: str) -> dict:
        # Copy the chosen fictional account, then issue a high-entropy random token.
        # Only its digest is persisted. Never log the raw token or include it in prompts.
        scope, persona = self.business.create_sandbox(persona_id)
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        # peer is supplied by the HTTP adapter's direct connection, not an arbitrary
        # forwarded header. Several visitors behind a proxy may share this limit.
        peer_hash = hashlib.sha256(peer.encode()).hexdigest()
        now = int(self.business.clock())
        failure = None
        with self.db.transaction() as conn:
            # Reclaim only expired retained data, never an in-flight request.
            conn.execute("DELETE FROM sandboxes WHERE expires_at<=? AND id NOT IN "
                         "(SELECT sandbox_id FROM customer_sessions WHERE lease_until>?)", (now, now))
            count = conn.execute("SELECT COUNT(*) FROM customer_sessions WHERE peer_hash=? AND created_at>?",
                                 (peer_hash, now - 3600)).fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM sandboxes").fetchone()[0]
            if count >= self.sessions_per_ip_hour or total > self.max_sandboxes:
                # Delete the just-created sandbox and COMMIT that cleanup before
                # raising. Raising here would roll the deletion back and leak demo rows.
                conn.execute("DELETE FROM sandboxes WHERE id=?", (scope.sandbox_id,))
                failure = BusinessError("SESSION_LIMIT", "Demo session limit reached; please try later", 429)
            else:
                conv_id = str(uuid.uuid4())
                conn.execute("INSERT INTO customer_sessions(token_hash,sandbox_id,customer_id,conv_id,created_at,expires_at,peer_hash) "
                             "VALUES(?,?,?,?,?,?,?)", (token_hash, scope.sandbox_id, scope.customer_id, conv_id, now, now + self.ttl, peer_hash))
        if failure:
            raise failure
        # The browser gets the raw token once, a server-bound conversation, and a TTL
        # in seconds. It does not get permission to choose another customer's scope.
        return {"token": token, "conv_id": conv_id, "expires_in": self.ttl,
                "customer": {"name": persona["name"], "persona_id": persona_id}}

    def authenticate(self, token: str) -> CustomerSession:
        # Hash the presented token using the same algorithm, then require an unexpired
        # row. A missing/expired token raises an error rather than falling back to anonymity.
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM customer_sessions WHERE token_hash=? AND expires_at>?",
                               (digest, int(self.business.clock()))).fetchone()
        if row is None:
            raise BusinessError("UNAUTHORIZED", "Your demo session has expired. Start a new demo conversation.", 401)
        return CustomerSession(digest, Scope(row["sandbox_id"], row["customer_id"]), row["conv_id"])

    def begin(self, session: CustomerSession, conv_id: str, request_id: str, message: str) -> dict:
        # Begin one retryable customer message. Return either a completed response or
        # a claim, an optional saved plan, and recent replies for language understanding.
        # All quota/replay/lease changes occur atomically under the database write slot.
        if conv_id != session.conv_id:
            raise BusinessError("NOT_FOUND", "Conversation not found", 404)
        now = int(self.business.clock())
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM customer_sessions WHERE token_hash=? AND expires_at>?",
                               (session.token_hash, now)).fetchone()
            if row is None:
                raise BusinessError("UNAUTHORIZED", "Demo session expired", 401)
            request = conn.execute("SELECT * FROM customer_requests WHERE token_hash=? AND request_id=?",
                                   (session.token_hash, request_id)).fetchone()
            digest = fingerprint(message)
            if request:
                # Replay is checked BEFORE quotas. A successful message whose reply
                # was lost must remain recoverable even if the session quota is now full.
                if request["fingerprint"] != digest:
                    raise BusinessError("IDEMPOTENCY_CONFLICT", "Request ID was already used for another message")
                if request["response_json"]:
                    return {"response": json.loads(request["response_json"])}
            if row["active_request"] and (row["active_request"] != request_id or row["lease_until"] > now):
                # Different messages cannot skip unfinished work. Even the same message
                # must wait for its existing attempt's lease to expire or be released.
                raise BusinessError("REQUEST_IN_PROGRESS", "An earlier message is still processing. Retry that message first.")
            if not request:
                # Count only a first submission. Retrying a row already in the database
                # does not consume another message allowance.
                if row["message_count"] >= self.max_messages:
                    raise BusinessError("MESSAGE_LIMIT", "Demo message limit reached. Start a new conversation.", 429)
                conn.execute("INSERT INTO customer_requests(token_hash,request_id,message,fingerprint,created_at) VALUES(?,?,?,?,?)",
                             (session.token_hash, request_id, message, digest, now))
                conn.execute("UPDATE customer_sessions SET message_count=message_count+1 WHERE token_hash=?", (session.token_hash,))
            # A claim distinguishes attempts of the SAME request. After 120 seconds,
            # a recovery attempt may acquire a new claim; old writers cannot save over it.
            claim = str(uuid.uuid4())
            conn.execute("UPDATE customer_sessions SET active_request=?,lease_until=?,claim_id=? WHERE token_hash=?",
                         (request_id, now + 120, claim, session.token_hash))
            # Load at most five completed messages, then reverse newest-first SQL order
            # into chronological order. Dictionary | adds the original customer message.
            recent = [json.loads(r["response_json"]) | {"message": r["message"]} for r in conn.execute(
                "SELECT message,response_json FROM customer_requests WHERE token_hash=? AND response_json IS NOT NULL "
                "ORDER BY rowid DESC LIMIT 5", (session.token_hash,))]
            return {"claim": claim, "plan": json.loads(request["plan_json"]) if request and request["plan_json"] else None,
                    "recent": list(reversed(recent))}

    def save_plan(self, session: CustomerSession, request_id: str, claim: str, plan: dict) -> dict:
        # Persist extraction BEFORE effects. COALESCE keeps an existing plan instead of
        # letting a later nondeterministic model result change the meaning of a retry.
        with self.db.transaction() as conn:
            active = conn.execute("SELECT claim_id FROM customer_sessions WHERE token_hash=?", (session.token_hash,)).fetchone()
            if active is None or active[0] != claim:
                # Compare the attempt identity, not just request_id, to reject old workers.
                raise BusinessError("STALE_REQUEST", "This message is being recovered by another request")
            conn.execute("UPDATE customer_requests SET plan_json=COALESCE(plan_json,?) WHERE token_hash=? AND request_id=?",
                         (json.dumps(plan), session.token_hash, request_id))
            return json.loads(conn.execute("SELECT plan_json FROM customer_requests WHERE token_hash=? AND request_id=?",
                                           (session.token_hash, request_id)).fetchone()[0])

    def complete(self, session: CustomerSession, request_id: str, claim: str, response: dict) -> dict:
        # Save the final public reply and clear the active lease together. This is a
        # separate transaction from the refund; operation replay bridges a crash between them.
        with self.db.transaction() as conn:
            active = conn.execute("SELECT claim_id FROM customer_sessions WHERE token_hash=?", (session.token_hash,)).fetchone()
            if active is None or active[0] != claim:
                raise BusinessError("STALE_REQUEST", "This message is being recovered by another request")
            conn.execute("UPDATE customer_requests SET response_json=? WHERE token_hash=? AND request_id=?",
                         (json.dumps(response), session.token_hash, request_id))
            conn.execute("UPDATE customer_sessions SET active_request=NULL,lease_until=0,claim_id=NULL WHERE token_hash=?",
                         (session.token_hash,))
        return response

    def release(self, session: CustomerSession, claim: str) -> None:
        # Only this attempt can shorten its lease. Do not erase active_request: the
        # next caller must retry that same message to reconcile any already-committed effect.
        # Keep the unresolved request identity: only a retry of it may run next.
        with self.db.transaction() as conn:
            conn.execute("UPDATE customer_sessions SET lease_until=0 WHERE token_hash=? AND claim_id=?", (session.token_hash, claim))
