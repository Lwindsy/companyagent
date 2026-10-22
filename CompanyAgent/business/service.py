"""All simulated side effects and approval decisions pass through this service."""
import hashlib
import json
import time
import uuid
from typing import Any, Callable

from business.db import Database
from business.models import BusinessError, PolicyConfig, Scope, refund_policy


# These are synthetic teaching accounts. Creating a sandbox copies their story into
# new database rows; selecting a persona is not real-world identity verification.
PERSONAS = (
    {"id": "p_duplicate", "name": "Anna Berg", "tagline": "Two payments for the same order.",
     "starter": "I was charged twice. Please refund the duplicate payment."},
    {"id": "p_b2b", "name": "Farid Haddad", "tagline": "A 450 EUR payment that needs supervisor review.",
     "starter": "Please refund my recent 450 EUR payment."},
    {"id": "p_cancel", "name": "Chloe Martin", "tagline": "A membership payment from 40 days ago.",
     "starter": "Please refund my membership renewal payment."},
)


def fingerprint(value: Any) -> str:
    # Serialize fields in a stable order before hashing. The digest detects reuse of
    # an operation ID with different content; it is not a password or authorization proof.
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class BusinessService:
    # db owns persistence; config supplies thresholds; clock returns UTC epoch seconds.
    # Injecting a clock lets tests move time forward without actually waiting for days.
    def __init__(self, db: Database, config: PolicyConfig | None = None, clock: Callable[[], float] = time.time):
        self.db = db
        self.config = config or PolicyConfig()
        self.clock = clock

    def create_sandbox(self, persona_id: str) -> tuple[Scope, dict]:
        # Find a supported story first. next(..., None) returns None if no ID matches.
        persona = next((p for p in PERSONAS if p["id"] == persona_id), None)
        if persona is None:
            raise BusinessError("NOT_FOUND", "Unknown demo customer", 404)
        # A random sandbox separates visitors even when both select the same persona.
        # Return (scope, persona description); callers must derive scope from trusted state.
        scope = Scope(str(uuid.uuid4()), persona_id)
        now = int(self.clock())
        with self.db.transaction() as conn:
            # Insert parents before children so foreign keys can find their owners.
            # ? placeholders bind tuple values as data instead of concatenating SQL.
            conn.execute("INSERT INTO sandboxes VALUES (?, ?, ?, ?)", (scope.sandbox_id, persona_id, now, now + 7 * 86400))
            conn.execute("INSERT INTO customers VALUES (?, ?, ?)", (scope.sandbox_id, scope.customer_id, persona["name"]))
            conn.execute("INSERT INTO subscriptions VALUES (?, ?, 0)", (scope.sandbox_id, scope.customer_id))
            # Amounts are cents. Story ages are relative to creation time so a fresh
            # demo still represents a recent payment or a 40-day-old expired payment.
            amount = 45000 if persona_id == "p_b2b" else 3990
            age = 40 * 86400 if persona_id == "p_cancel" else 86400
            rows = [(scope.sandbox_id, scope.customer_id, "ch_1", "order_1042", amount, "EUR", now - age)]
            if persona_id == "p_duplicate":
                # The second payment is four minutes later for the same order.
                # Duplicate detection will verify these facts rather than trust the message.
                rows.append((scope.sandbox_id, scope.customer_id, "ch_2", "order_1042", amount, "EUR", now - age + 240))
            conn.executemany("INSERT INTO charges VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        return scope, dict(persona)

    def overview(self, scope: Scope) -> dict:
        # Build display facts for this scope only. connect() closes resources but does
        # not group these SELECTs into one explicit snapshot transaction.
        with self.db.connect() as conn:
            customer = conn.execute("SELECT name FROM customers WHERE sandbox_id=? AND id=?",
                                    (scope.sandbox_id, scope.customer_id)).fetchone()
            if customer is None:
                raise BusinessError("NOT_FOUND", "Customer not found", 404)
            subscription = conn.execute("SELECT cancel_at_period_end FROM subscriptions WHERE sandbox_id=? AND customer_id=?",
                                        (scope.sandbox_id, scope.customer_id)).fetchone()
            # EXISTS yields 0 or 1 according to actual refund rows. We keep the original
            # charge and derive its refunded flag rather than erase the charge history.
            charges = [dict(r) for r in conn.execute(
                "SELECT c.id, c.order_id, c.amount_cents, c.currency, c.created_at, "
                "EXISTS(SELECT 1 FROM refunds r WHERE r.sandbox_id=c.sandbox_id AND r.customer_id=c.customer_id "
                "AND r.charge_id=c.id) AS refunded FROM charges c WHERE c.sandbox_id=? AND c.customer_id=? ORDER BY c.created_at",
                (scope.sandbox_id, scope.customer_id))]
            return {"name": customer["name"], "cancel_at_period_end": bool(subscription[0]), "charges": charges}

    def _charge(self, conn, scope: Scope, charge_id: str):
        # A charge ID can repeat in other sandboxes, so all three identity parts matter.
        # fetchone returns None when absent. Missing and foreign-owned IDs look identical.
        row = conn.execute("SELECT * FROM charges WHERE sandbox_id=? AND customer_id=? AND id=?",
                           (scope.sandbox_id, scope.customer_id, charge_id)).fetchone()
        if row is None:
            raise BusinessError("NOT_FOUND", "Payment not found", 404)
        return row

    def _refund_decision(self, conn, scope: Scope, charge, reason: str, approved: bool, action_id: str = ""):
        # Load all mutable facts using the caller's transaction connection. A new
        # connection here could observe a different point in time or compete for locks.
        own = (scope.sandbox_id, scope.customer_id)
        refunded = conn.execute("SELECT 1 FROM refunds WHERE sandbox_id=? AND customer_id=? AND charge_id=?",
                                (*own, charge["id"])).fetchone() is not None
        # Exclude the action being approved, or it would incorrectly block itself.
        # Expired pending actions do not prevent a fresh request for the same charge.
        pending = conn.execute("SELECT args_json FROM actions WHERE sandbox_id=? AND customer_id=? "
                               "AND tool='refund' AND status='pending' AND id<>? AND expires_at>?",
                               (*own, action_id, int(self.clock()))).fetchall()
        pending_other = any(json.loads(r[0])["charge_id"] == charge["id"] for r in pending)
        # SUM totals completed refunds in a rolling 30-day window; no rows means zero.
        # *own expands the two scope values into the parameter tuple in SQL order.
        total = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM refunds WHERE sandbox_id=? AND customer_id=? AND created_at>=?",
                             (*own, int(self.clock()) - 30 * 86400)).fetchone()[0]
        # Only the later payment of a verified pair can be auto-refunded.
        earlier = conn.execute("SELECT 1 FROM charges WHERE sandbox_id=? AND customer_id=? AND order_id=? "
                               "AND amount_cents=? AND currency=? AND created_at<? AND created_at>=? LIMIT 1",
                               (*own, charge["order_id"], charge["amount_cents"], charge["currency"],
                                charge["created_at"], charge["created_at"] - 900)).fetchone()
        # The policy receives facts, not a connection. It decides without doing I/O.
        return refund_policy(age_seconds=int(self.clock()) - charge["created_at"], amount_cents=charge["amount_cents"],
                             refunded=refunded, pending_other=pending_other, monthly_total=total,
                             verified_duplicate=reason == "duplicate_charge" and earlier is not None,
                             approved=approved, config=self.config)

    def _event(self, conn, scope, conv_id, action_id, kind, payload):
        # Store a history entry in the SAME transaction as the state change. json.dumps
        # converts the small payload dictionary into text for the payload_json column.
        # These rows aid troubleshooting; they are not a tamper-proof external audit log.
        conn.execute("INSERT INTO events(sandbox_id, customer_id, conv_id, action_id, kind, payload_json, at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (scope.sandbox_id, scope.customer_id, conv_id, action_id, kind, json.dumps(payload), int(self.clock())))

    def _apply(self, conn, scope, action_id, tool, args):
        # Only local simulated side effects live here. Do not commit: the outer
        # operation must commit the effect, action state, and event together.
        if tool == "refund":
            charge = self._charge(conn, scope, args["charge_id"])
            conn.execute("INSERT INTO refunds VALUES (?, ?, ?, ?, ?, ?)",
                         (scope.sandbox_id, scope.customer_id, charge["id"], action_id, charge["amount_cents"], int(self.clock())))
            return {"changed": True}
        # For renewal, compare the old 0/1 flag with the requested one. Asking for the
        # existing value is a valid no-op, reported as changed=False rather than an error.
        current = conn.execute("SELECT cancel_at_period_end FROM subscriptions WHERE sandbox_id=? AND customer_id=?",
                               (scope.sandbox_id, scope.customer_id)).fetchone()
        value = int(args["cancel_at_period_end"])
        conn.execute("UPDATE subscriptions SET cancel_at_period_end=? WHERE sandbox_id=? AND customer_id=?",
                     (value, scope.sandbox_id, scope.customer_id))
        return {"changed": current[0] != value}

    def execute(self, scope: Scope, conv_id: str, operation_id: str, tool: str, args: dict) -> dict:
        # Inputs come from a trusted adapter: scope identifies the owner, conv_id the
        # conversation, and operation_id one retryable business operation. The model
        # cannot choose identity or operation IDs. Return a public action dictionary.
        if not operation_id or len(operation_id) > 128:
            raise BusinessError("INVALID_ARGUMENTS", "A stable operation ID is required", 422)
        if tool == "refund":
            # set(args) checks the EXACT allowed keys, including rejecting extra keys.
            if set(args) != {"charge_id", "reason_code"} or args["reason_code"] not in {"duplicate_charge", "other"}:
                raise BusinessError("INVALID_ARGUMENTS", "Invalid refund arguments", 422)
        elif tool == "renewal":
            # type(...) is bool rejects strings such as "false" and integers such as 1.
            if set(args) != {"cancel_at_period_end"} or type(args["cancel_at_period_end"]) is not bool:
                raise BusinessError("INVALID_ARGUMENTS", "Invalid renewal arguments", 422)
        else:
            raise BusinessError("NOT_ALLOWED", "Unsupported operation", 422)
        # Include the conversation in the fingerprint, so an ID cannot be moved to
        # another conversation while pretending it is the original request.
        digest = fingerprint([tool, args, conv_id])
        now = int(self.clock())
        with self.db.transaction() as conn:
            existing = conn.execute("SELECT * FROM actions WHERE sandbox_id=? AND customer_id=? AND operation_id=?",
                                    (scope.sandbox_id, scope.customer_id, operation_id)).fetchone()
            if existing:
                # Replaying the same operation returns its recorded result before quota
                # checks. A lost reply must not consume another write allowance.
                if existing["fingerprint"] != digest:
                    raise BusinessError("IDEMPOTENCY_CONFLICT", "Operation ID already used with different arguments")
                return self._public(existing)
            # Persisted rows survive restarts; a process-local counter would not.
            count = conn.execute("SELECT COUNT(*) FROM actions WHERE sandbox_id=? AND customer_id=? AND conv_id=?",
                                 (scope.sandbox_id, scope.customer_id, conv_id)).fetchone()[0]
            if count >= self.config.max_writes_per_conversation:
                raise BusinessError("WRITE_LIMIT", "Please contact human support for further changes", 429)
            amount = None
            if tool == "refund":
                charge = self._charge(conn, scope, args["charge_id"])
                amount = charge["amount_cents"]
                decision = self._refund_decision(conn, scope, charge, args["reason_code"], False)
                status, reason = decision.outcome, decision.reason
                summary = f"Refund {amount / 100:.2f} EUR for {charge['id']}"
            else:
                target = conn.execute("SELECT 1 FROM subscriptions WHERE sandbox_id=? AND customer_id=?",
                                      (scope.sandbox_id, scope.customer_id)).fetchone()
                if target is None:
                    raise BusinessError("NOT_FOUND", "Subscription not found", 404)
                status, reason = "executed", "RENEWAL_UPDATED"
                summary = "Cancel membership renewal" if args["cancel_at_period_end"] else "Restore membership renewal"
            # Even a rule denial gets an action explaining the attempted operation.
            # No refund row is created for pending or denied outcomes.
            action_id = str(uuid.uuid4())
            conn.execute("INSERT INTO actions(id,sandbox_id,customer_id,conv_id,operation_id,fingerprint,tool,args_json,"
                         "status,reason_code,amount_cents,summary,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (action_id, scope.sandbox_id, scope.customer_id, conv_id, operation_id, digest, tool,
                          json.dumps(args, sort_keys=True), status, reason, amount, summary, now,
                          now + self.config.approval_ttl_hours * 3600))
            if status == "executed":
                # Reuse conn so an exception after _apply also rolls back the effect.
                result = self._apply(conn, scope, action_id, tool, args)
                conn.execute("UPDATE actions SET result_json=? WHERE id=?", (json.dumps(result), action_id))
            self._event(conn, scope, conv_id, action_id, status, {"reason_code": reason})
            return self._public(conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone())

    def decide(self, action_id: str, decision: str, actor: str, note: str = "", scope: Scope | None = None) -> dict:
        # The HTTP adapter authenticates actor; the string "admin" alone grants nothing.
        # Demo callers supply scope, while authenticated staff can act across scopes.
        # The returned action reflects the state committed when this with block exits.
        if decision not in {"approve", "reject"}:
            raise BusinessError("INVALID_ARGUMENTS", "Invalid decision", 422)
        now = int(self.clock())
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if row is None or (scope and (row["sandbox_id"], row["customer_id"]) != (scope.sandbox_id, scope.customer_id)):
                raise BusinessError("NOT_FOUND", "Action not found", 404)
            if row["status"] != "pending":
                raise BusinessError("ALREADY_DECIDED", "This action has already been processed")
            own = Scope(row["sandbox_id"], row["customer_id"])
            # An expired decision becomes a persisted state; it is not an exception
            # here, because raising inside the transaction would undo that expiry.
            if row["expires_at"] <= now:
                status, reason = "expired", "APPROVAL_EXPIRED"
            elif decision == "reject":
                if not note.strip():
                    raise BusinessError("NOTE_REQUIRED", "A rejection reason is required", 422)
                status, reason = "rejected", "SUPERVISOR_REJECTED"
            else:
                if (row["amount_cents"] or 0) > self.config.high_value_cents and not note.strip():
                    raise BusinessError("NOTE_REQUIRED", "A note is required for high-value approval", 422)
                args = json.loads(row["args_json"])
                charge = self._charge(conn, own, args["charge_id"])
                # Recheck CURRENT facts: approval does not freeze the original window
                # or bypass hard rules. Passing action_id excludes this pending action.
                policy = self._refund_decision(conn, own, charge, args["reason_code"], True, action_id)
                status = "executed" if policy.outcome == "executed" else "failed"
                reason = policy.reason
                if status == "executed":
                    result = self._apply(conn, own, action_id, "refund", args)
                    conn.execute("UPDATE actions SET result_json=? WHERE id=?", (json.dumps(result), action_id))
            # A rule failure is a committed failed outcome. A Python/database exception
            # instead rolls the whole transaction back, leaving the action retryable.
            conn.execute("UPDATE actions SET status=?, reason_code=?, decided_at=?, decided_by=?, decision_note=? WHERE id=?",
                         (status, reason, now, actor, note.strip(), action_id))
            self._event(conn, own, row["conv_id"], action_id, status, {"actor": actor, "reason_code": reason})
            return self._public(conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone())

    def list_actions(self, scope: Scope | None = None, conv_id: str | None = None) -> list[dict]:
        # Listing can expire pending actions, so it needs a write transaction too.
        # Omitting scope is an administrator capability; adapters enforce that boundary.
        with self.db.transaction() as conn:
            # Expiry is a real state transition with an event, not merely a UI label.
            query, params = "SELECT * FROM actions WHERE status='pending' AND expires_at<=?", [int(self.clock())]
            if scope:
                query += " AND sandbox_id=? AND customer_id=?"
                params += [scope.sandbox_id, scope.customer_id]
            for row in conn.execute(query, params).fetchall():
                conn.execute("UPDATE actions SET status='expired',reason_code='APPROVAL_EXPIRED' WHERE id=?", (row["id"],))
                self._event(conn, Scope(row["sandbox_id"], row["customer_id"]), row["conv_id"], row["id"], "expired", {})
            query, params = "SELECT * FROM actions WHERE 1=1", []
            if scope:
                query += " AND sandbox_id=? AND customer_id=?"
                params += [scope.sandbox_id, scope.customer_id]
            if conv_id:
                query += " AND conv_id=?"
                params.append(conv_id)
            # rowid breaks ties for events created in the same second. Bound the result
            # size to 100 rather than pretending this is a full pagination API.
            return [self._public(r) for r in conn.execute(query + " ORDER BY created_at DESC, rowid DESC LIMIT 100", params)]

    @staticmethod
    def _public(row) -> dict:
        # A projection exposes only fields needed by clients; internal fingerprints,
        # raw arguments, and staff notes are not copied into every public response.
        fields = ("id", "conv_id", "tool", "status", "reason_code", "amount_cents", "summary", "created_at", "expires_at")
        return {key: row[key] for key in fields}

    def handoff(self, scope: Scope, conv_id: str, summary: str) -> dict:
        # Reuse an open ticket in this conversation. Creating a row records a case;
        # it does not connect a live human or send a message outside this application.
        with self.db.transaction() as conn:
            row = conn.execute("SELECT id FROM tickets WHERE sandbox_id=? AND customer_id=? AND conv_id=? AND status='open'",
                               (scope.sandbox_id, scope.customer_id, conv_id)).fetchone()
            if row:
                return {"id": row[0], "status": "open"}
            ticket_id = str(uuid.uuid4())
            conn.execute("INSERT INTO tickets(id,sandbox_id,customer_id,conv_id,summary,created_at) VALUES(?,?,?,?,?,?)",
                         (ticket_id, scope.sandbox_id, scope.customer_id, conv_id, summary[:2000], int(self.clock())))
            self._event(conn, scope, conv_id, None, "handoff", {"ticket_id": ticket_id})
            return {"id": ticket_id, "status": "open"}
