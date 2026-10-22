"""SQLite run journal, fencing leases and budget reservations.

The business Database supplies short BEGIN IMMEDIATE transactions. These tables
share its file so identity/message quotas can be checked atomically. LangGraph's
checkpointer uses a separate file: tool/model journals bridge that non-atomic gap.
Money is integer microdollars (one million units per USD), never binary floats.
"""
import hashlib
import json
import time
import uuid

from business.models import BusinessError


TERMINAL = {"completed", "failed", "cancelled", "budget_exhausted"}


def digest(value):
    # Reading note: Serialize a JSON-compatible value in a stable order, then hash the bytes.
    # The digest detects reuse of an ID with different content; it is not encryption and must
    # not be used to hide credentials. Lists keep their order because changing message/tool
    # order can change meaning.
    """Return a stable identifier for JSON data, not executable or secret data."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class RunStore:
    """Own local runtime metadata; db is the existing scoped business database."""

    def __init__(self, db, clock=time.time):
        # Reading note: db is the existing business Database object; the same file lets
        # journals share the business transaction boundary where needed. clock returns seconds
        # since the Unix epoch and is injectable for expiry tests. Create missing
        # tables/indexes inside a transaction; IF NOT EXISTS preserves previous rows. No
        # destructive migration or financial operation occurs here.
        self.db, self.clock = db, clock
        # All SQL below is static application schema. No model output becomes SQL.
        with db.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS agent_runs(
                  id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, sandbox TEXT NOT NULL,
                  customer TEXT NOT NULL, conv TEXT NOT NULL, request TEXT NOT NULL,
                  fingerprint TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL,
                  version INTEGER NOT NULL DEFAULT 0, lease_owner TEXT, lease_until REAL DEFAULT 0,
                  cancel INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
                  payload TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}',
                  UNIQUE(token_hash,request));
                CREATE UNIQUE INDEX IF NOT EXISTS agent_one_active ON agent_runs(sandbox,customer,conv)
                  WHERE status IN ('queued','running','waiting_user','waiting_approval');
                CREATE TABLE IF NOT EXISTS agent_events(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT NOT NULL REFERENCES agent_runs(id),
                  kind TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_calls(
                  run TEXT NOT NULL REFERENCES agent_runs(id), id TEXT NOT NULL,
                  kind TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                  result TEXT, held INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(run,id));
                CREATE TABLE IF NOT EXISTS agent_budgets(
                  id TEXT PRIMARY KEY, limit_micro INTEGER NOT NULL, used_micro INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS agent_inputs(
                  run TEXT NOT NULL REFERENCES agent_runs(id), request TEXT NOT NULL,
                  fingerprint TEXT NOT NULL, PRIMARY KEY(run,request));
            """)

    def _event(self, conn, run, kind, data):
        # Events contain public summaries, not raw prompts or bearer credentials.
        # Reading note: conn is the caller open transaction, run is a saved run ID, kind names
        # an event, and data is a JSON-compatible PUBLIC summary. Insert with epoch seconds
        # from clock. Sharing the transaction means a failed operation rolls back its event
        # too; raw model continuation blocks and bearer credentials do not belong here.
        conn.execute("INSERT INTO agent_events(run,kind,data,created) VALUES(?,?,?,?)",
                     (run, kind, json.dumps(data), self.clock()))

    def create(self, session, request, message, limit_micro=250000, grant=None):
        # Reading note: session is authenticated scope, request is the stable browser retry
        # ID, message is user text, and grant is its exact optional write target. limit_micro
        # defaults to 250000 microdollars (0.25 USD) per run. Check a prior fingerprint before
        # charging a message quota, then reject expired/busy sessions. Insert run, budget and
        # event atomically and return the internal row. Reusing an ID with changed text/grant
        # is an error, not a new action.
        """Create one run; retry returns its original ID before charging quota.

        session has already been authenticated. The database check below repeats
        expiry and ownership rather than trusting a caller-supplied customer ID.
        """
        with self.db.transaction() as conn:
            old = conn.execute("SELECT * FROM agent_runs WHERE token_hash=? AND request=?",
                               (session.token_hash, request)).fetchone()
            if old:
                if old["fingerprint"] != digest([message, grant or {}]):
                    raise BusinessError("IDEMPOTENCY_CONFLICT", "Request ID already has another message")
                return dict(old)
            row = conn.execute("SELECT * FROM customer_sessions WHERE token_hash=? AND expires_at>?",
                               (session.token_hash, self.clock())).fetchone()
            if row is None:
                raise BusinessError("UNAUTHORIZED", "Session expired", 401)
            if row["message_count"] >= 40:
                raise BusinessError("MESSAGE_LIMIT", "Conversation message limit reached", 429)
            if row["active_request"]:
                raise BusinessError("REQUEST_IN_PROGRESS", "Finish the earlier business request first")
            if conn.execute("SELECT 1 FROM agent_runs WHERE sandbox=? AND customer=? AND conv=? "
                            "AND status IN ('queued','running','waiting_user','waiting_approval')",
                            (session.scope.sandbox_id, session.scope.customer_id, session.conv_id)).fetchone():
                raise BusinessError("REQUEST_IN_PROGRESS", "Continue or cancel the current agent run")
            run = str(uuid.uuid4())
            payload = {"schema_version": 1, "diagnostic": "degraded", "grant": grant or {}}
            conn.execute("INSERT INTO agent_runs(id,token_hash,sandbox,customer,conv,request,fingerprint,message,"
                         "status,created,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (run, session.token_hash, session.scope.sandbox_id, session.scope.customer_id,
                          session.conv_id, request, digest([message, grant or {}]), message, "queued", self.clock(), json.dumps(payload)))
            conn.execute("UPDATE customer_sessions SET message_count=message_count+1 WHERE token_hash=?", (session.token_hash,))
            conn.execute("INSERT INTO agent_budgets VALUES(?,?,0)", (run, limit_micro))
            self._event(conn, run, "queued", {})
            return dict(conn.execute("SELECT * FROM agent_runs WHERE id=?", (run,)).fetchone())

    def get(self, run, session=None):
        # Reading note: run is a UUID-like saved ID; optional session limits visibility to its
        # token hash and conversation. Return a plain dictionary for internal engine use.
        # Missing or mismatched rows raise the same NOT_FOUND error so another customer cannot
        # discover a run. HTTP callers authenticate expiry separately and expose public rather
        # than this raw row.
        """Fetch an internal row; public adapters must use public() to hide prompts."""
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run,)).fetchone()
        if row is None or (session and (row["token_hash"] != session.token_hash or row["conv"] != session.conv_id)):
            raise BusinessError("NOT_FOUND", "Agent run not found", 404)
        return dict(row)

    def public(self, run, session=None):
        # Reading note: Read the scoped run and its budget. Return only ID, status, version,
        # parsed public result and used-or-reserved USD; dividing integer microdollars by one
        # million is presentation conversion. This field includes conservative unresolved
        # holds, so it must not be labeled a settled invoice.
        """Expose only scoped result/status, counters and a monotonically changing version."""
        row = self.get(run, session)
        with self.db.connect() as conn:
            budget = dict(conn.execute("SELECT * FROM agent_budgets WHERE id=?", (run,)).fetchone())
        return {"id": run, "status": row["status"], "version": row["version"],
                "result": json.loads(row["result"]), "used_or_reserved_usd": budget["used_micro"] / 1e6}

    def events(self, run, after=0):
        # The HTTP layer authenticates ownership first; pagination bounds memory use.
        # Reading note: after is the last seen integer sequence, initially zero. SQL returns
        # at most 100 newer events in ascending order; the client uses their sequence to
        # reconnect without duplication. Ownership must already be checked by the HTTP
        # adapter. The sequence is global across runs, so gaps are normal.
        with self.db.connect() as conn:
            return [{"seq": r["seq"], "kind": r["kind"], "data": json.loads(r["data"])} for r in conn.execute(
                "SELECT * FROM agent_events WHERE run=? AND seq>? ORDER BY seq LIMIT 100", (run, after))]

    def claim(self, run, owner, seconds=120):
        # Reading note: run and owner identify queued work and this worker. seconds is lease
        # duration, default 120 seconds, longer than one active slice. Inside a write
        # transaction reject an unexpired owner, otherwise increment version and return that
        # fencing number. None means no claim was acquired; the caller must not execute work.
        """Grant a fencing version, or None if a live owner already holds the run."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run,)).fetchone()
            if not row or row["status"] not in {"queued", "running"} or row["lease_until"] > self.clock():
                return None
            conn.execute("UPDATE agent_runs SET status='running',version=version+1,lease_owner=?,lease_until=? WHERE id=?",
                         (owner, self.clock() + seconds, run))
            return row["version"] + 1

    def guard(self, conn, run, owner, version):
        # Reading note: Using the caller transaction, compare owner, version and lease
        # deadline and join the session expiry. Raise before external work if ownership
        # changed, cancellation was requested or authentication expired. A version is a
        # monotonically increasing integer token, not a software release number. Guarding
        # cannot undo an operation that already committed.
        """Reject stale workers and expired sessions before another external action."""
        row = conn.execute("SELECT r.*,s.expires_at FROM agent_runs r JOIN customer_sessions s "
                           "ON r.token_hash=s.token_hash WHERE r.id=?", (run,)).fetchone()
        if (not row or row["lease_owner"] != owner or row["version"] != version
                or row["lease_until"] <= self.clock()):
            raise BusinessError("STALE_RUN", "A different worker owns this run")
        if row["cancel"]:
            raise BusinessError("CANCELLED", "Run cancelled")
        if row["expires_at"] <= self.clock():
            raise BusinessError("UNAUTHORIZED", "Session expired", 401)

    def update(self, run, owner, version, status, result=None):
        # Reading note: owner/version must still match before saving a worker status/result.
        # Ignore a stale completion instead of overwriting a new owner. A cancellation flag
        # wins over an ordinary result; clear the lease, advance version and append the event
        # in the same transaction. This updates run bookkeeping, not the financial ledger.
        """Finish a worker slice without overwriting a later cancellation/owner."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run,)).fetchone()
            if row["lease_owner"] != owner or row["version"] != version:
                return
            status = "cancelled" if row["cancel"] else status
            conn.execute("UPDATE agent_runs SET status=?,result=?,lease_owner=NULL,lease_until=0,version=version+1 WHERE id=?",
                         (status, json.dumps(result or {}), run))
            self._event(conn, run, status, result or {})

    def cancel(self, run, session):
        # Reading note: session scopes the requested run. Mark cancellation immediately unless
        # a live worker still needs its current version to reconcile an already-started
        # operation. A queued/expired-owner run can become cancelled now. Return public
        # status; cancellation stops future work but is never a financial rollback.
        """Stop future work; an in-flight database operation may still have committed."""
        self.get(run, session)
        with self.db.transaction() as conn:
            conn.execute("UPDATE agent_runs SET cancel=1,status=CASE WHEN status='running' AND lease_until>? THEN status ELSE 'cancelled' END,"
                         "version=CASE WHEN status='running' AND lease_until>? THEN version ELSE version+1 END WHERE id=? "
                         "AND status NOT IN ('completed','failed','budget_exhausted')", (self.clock(), self.clock(), run))
            self._event(conn, run, "cancellation_requested", {})
        return self.public(run, session)

    def resume(self, run, session, request, message, version, grant=None):
        # Reading note: request is a stable continuation ID and version is the browser
        # observed run version. Replaying identical input returns the saved run; changed
        # text/grant conflicts. Only waiting_user accepts input, never waiting_approval. Check
        # session/quota, replace its grant and increment input_generation so one new financial
        # capability belongs only to this input. Persist the input fingerprint and queue
        # transition together.
        """Accept one versioned user continuation; repeated request IDs are idempotent."""
        self.get(run, session)
        with self.db.transaction() as conn:
            prior = conn.execute("SELECT fingerprint FROM agent_inputs WHERE run=? AND request=?", (run, request)).fetchone()
            if prior:
                if prior[0] != digest([message, grant or {}]):
                    raise BusinessError("IDEMPOTENCY_CONFLICT", "Input ID already used")
                return self.get(run)
            row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run,)).fetchone()
            if row["status"] != "waiting_user" or row["version"] != version:
                raise BusinessError("STALE_RUN", "Run is not waiting for this input")
            session_row = conn.execute("SELECT message_count,expires_at FROM customer_sessions WHERE token_hash=?",
                                       (session.token_hash,)).fetchone()
            if session_row[1] <= self.clock() or session_row[0] >= 40:
                raise BusinessError("MESSAGE_LIMIT", "Session expired or message limit reached", 429)
            payload = json.loads(row["payload"])
            payload["resume_input"] = message
            payload["grant"] = grant or {}
            payload["input_generation"] = payload.get("input_generation", 0) + 1
            conn.execute("INSERT INTO agent_inputs VALUES(?,?,?)", (run, request, digest([message, grant or {}])))
            conn.execute("UPDATE agent_runs SET payload=?,status='queued',version=version+1 WHERE id=?",
                         (json.dumps(payload), run))
            conn.execute("UPDATE customer_sessions SET message_count=message_count+1 WHERE token_hash=?", (session.token_hash,))
            self._event(conn, run, "user_input", {})
        return self.get(run)

    def candidates(self):
        # Reading note: Return persisted IDs for queued, running or waiting_approval runs. The
        # worker checks leases and real approval facts before doing work. This simple local
        # scanner favors inspectable recovery over an external message broker; it is not a
        # distributed high-throughput queue.
        """Recover persisted runnable work after process restart; never busy-poll approvals."""
        with self.db.connect() as conn:
            return [r[0] for r in conn.execute("SELECT id FROM agent_runs WHERE status IN ('queued','running','waiting_approval')")]

    def journal(self, run, key, kind, arguments, owner, version, held=0):
        # Reading note: key identifies one model/tool call; kind selects its class and
        # arguments is hashed to detect changed retries. held is integer microdollars, zero
        # for non-model calls. Guard ownership, then return a completed prior result or record
        # a new started call and its hold atomically. An unfinished model call is uncertain
        # and stops; an unfinished business tool may safely reconcile through its stable
        # operation ID.
        """Persist call identity before execution; return a saved result on replay.

        An unfinished model call is uncertain and must not be automatically repeated.
        Unfinished local business calls may be reconciled with their stable operation ID.
        """
        with self.db.transaction() as conn:
            self.guard(conn, run, owner, version)
            old = conn.execute("SELECT * FROM agent_calls WHERE run=? AND id=?", (run, key)).fetchone()
            fingerprint = digest(arguments)
            if old:
                if old["fingerprint"] != fingerprint:
                    raise BusinessError("IDEMPOTENCY_CONFLICT", "Call changed during replay")
                if old["status"] == "completed":
                    return json.loads(old["result"])
                if kind == "model":
                    raise BusinessError("UNCERTAIN_MODEL", "Interrupted model response; reservation retained")
                return None
            if held:
                budget = conn.execute("SELECT * FROM agent_budgets WHERE id=?", (run,)).fetchone()
                if budget["used_micro"] + held > budget["limit_micro"]:
                    raise BusinessError("BUDGET_EXHAUSTED", "Run budget exhausted", 429)
                conn.execute("UPDATE agent_budgets SET used_micro=used_micro+? WHERE id=?", (held, run))
            conn.execute("INSERT INTO agent_calls VALUES(?,?,?,?,?,?,?)", (run, key, kind, fingerprint, "started", None, held))
            self._event(conn, run, kind + "_started", {"call_id": key, "name": arguments.get("name", kind)})
        return None

    def complete_call(self, run, key, result, measured=0):
        # Reading note: result is the received JSON-compatible result and measured is actual
        # estimated microdollars, default zero for tools. If already completed, do nothing.
        # Settle only within the original reservation; an invalid amount stops without
        # releasing that hold. Persist the result even if cancellation arrived during the
        # call, then emit elapsed milliseconds and public usage/status. This records completed
        # work and grants no permission for another call.
        """Persist a received result even if cancellation arrived during the call.

        Completion of an already-started call is reconciliation, not permission to
        start another. The next journal() will recheck the fencing/cancellation guard.
        """
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM agent_calls WHERE run=? AND id=?", (run, key)).fetchone()
            if row["status"] == "completed":
                return
            if row["held"]:
                if not 0 <= measured <= row["held"]:
                    raise BusinessError("BUDGET_EXHAUSTED", "Usage exceeded conservative reservation", 429)
                conn.execute("UPDATE agent_budgets SET used_micro=used_micro-?+? WHERE id=?", (row["held"], measured, run))
            conn.execute("UPDATE agent_calls SET status='completed',result=? WHERE run=? AND id=?",
                         (json.dumps(result), run, key))
            # Summaries intentionally omit native provider continuation blocks.
            started = conn.execute("SELECT created FROM agent_events WHERE run=? AND kind LIKE '%_started' "
                                   "AND json_extract(data,'$.call_id')=? ORDER BY seq DESC LIMIT 1", (run, key)).fetchone()
            summary = {"call_id": key, "cost_micro": measured,
                       "elapsed_ms": round(max(0, self.clock() - started[0]) * 1000, 1) if started else 0}
            if row["kind"] == "model":
                summary.update({"input_tokens": result.get("input_tokens", 0), "output_tokens": result.get("output_tokens", 0)})
            else:
                summary["status"] = result.get("action", {}).get("status", result.get("error", "observed"))
            self._event(conn, run, row["kind"] + "_completed", summary)
