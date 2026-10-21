-- A migration is a numbered set of table-layout changes. This file runs once for
-- version 1; db.py records that version in the same transaction as these statements.
-- TEXT stores strings; INTEGER stores whole numbers. Times are UTC epoch seconds,
-- monetary values are euro cents, and NOT NULL rejects a missing SQL NULL value.
-- These tables store simulated business facts, not real payment-provider transfers.
CREATE TABLE sandboxes (
    id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
-- Each visitor receives a new sandbox. The same persona/customer ID can appear in
-- several sandboxes, so the pair (sandbox_id, id) is the customer's unique address.
-- REFERENCES requires an existing parent. ON DELETE CASCADE removes dependent
-- demo rows if their parent is deleted; it is not a customer authorization rule.
CREATE TABLE customers (
    sandbox_id TEXT NOT NULL REFERENCES sandboxes(id) ON DELETE CASCADE,
    id TEXT NOT NULL, name TEXT NOT NULL,
    PRIMARY KEY (sandbox_id, id)
);
-- A charge is an original debit, retained even after a refund is recorded elsewhere.
-- The three-column PRIMARY KEY prevents duplicate IDs within one customer's scope.
-- CHECK rejects values violating these expressions; application validation is still
-- needed, and these ordinary SQLite tables are not STRICT type-validation schemas.
CREATE TABLE charges (
    sandbox_id TEXT NOT NULL, customer_id TEXT NOT NULL, id TEXT NOT NULL,
    order_id TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
    currency TEXT NOT NULL CHECK(currency = 'EUR'), created_at INTEGER NOT NULL,
    PRIMARY KEY (sandbox_id, customer_id, id),
    FOREIGN KEY (sandbox_id, customer_id) REFERENCES customers(sandbox_id, id) ON DELETE CASCADE
);
-- Store one renewal setting per customer. SQLite represents this flag as 0 or 1:
-- 1 means cancel at period end, while 0 means renewal remains enabled.
CREATE TABLE subscriptions (
    sandbox_id TEXT NOT NULL, customer_id TEXT NOT NULL,
    cancel_at_period_end INTEGER NOT NULL CHECK(cancel_at_period_end IN (0, 1)),
    PRIMARY KEY (sandbox_id, customer_id),
    FOREIGN KEY (sandbox_id, customer_id) REFERENCES customers(sandbox_id, id) ON DELETE CASCADE
);
-- An action records an attempt and its latest state. pending/denied rows do not
-- imply that money was refunded. JSON columns contain serialized small dictionaries.
-- operation_id identifies a retryable request; fingerprint detects altered content.
-- UNIQUE forbids using one operation ID twice in the same customer scope. The
-- service normally finds and returns the existing row before hitting this constraint.
CREATE TABLE actions (
    id TEXT PRIMARY KEY, sandbox_id TEXT NOT NULL, customer_id TEXT NOT NULL,
    conv_id TEXT NOT NULL, operation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
    tool TEXT NOT NULL, args_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','executed','denied','rejected','expired','failed')),
    reason_code TEXT NOT NULL, amount_cents INTEGER, summary TEXT NOT NULL,
    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, decided_at INTEGER,
    decided_by TEXT, decision_note TEXT, result_json TEXT,
    UNIQUE (sandbox_id, customer_id, operation_id),
    FOREIGN KEY (sandbox_id, customer_id) REFERENCES customers(sandbox_id, id) ON DELETE CASCADE
);
-- An index is an additional lookup structure: it can speed compatible queries but
-- costs space and write maintenance. This one supports scope/conversation lookups.
CREATE INDEX actions_conv ON actions(sandbox_id, customer_id, conv_id, created_at);
-- A refund is an executed simulated effect. Its charge-level key allows at most
-- ONE full refund per charge, even when callers choose different operation IDs.
-- This design does not support partial refunds; they need a different amount model.
-- The composite foreign key points to the charge in the SAME customer scope.
-- Other rules, such as matching the action's owner and amount, remain service checks.
CREATE TABLE refunds (
    sandbox_id TEXT NOT NULL, customer_id TEXT NOT NULL, charge_id TEXT NOT NULL,
    action_id TEXT NOT NULL UNIQUE REFERENCES actions(id) ON DELETE CASCADE,
    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0), created_at INTEGER NOT NULL,
    PRIMARY KEY (sandbox_id, customer_id, charge_id),
    FOREIGN KEY (sandbox_id, customer_id, charge_id)
        REFERENCES charges(sandbox_id, customer_id, id) ON DELETE CASCADE
);
-- A ticket records a handoff request. open/closed describes the case, not whether
-- a human is currently online. summary is supplied by the workflow and length-bounded.
CREATE TABLE tickets (
    id TEXT PRIMARY KEY, sandbox_id TEXT NOT NULL, customer_id TEXT NOT NULL,
    conv_id TEXT NOT NULL, summary TEXT NOT NULL, created_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
    FOREIGN KEY (sandbox_id, customer_id) REFERENCES customers(sandbox_id, id) ON DELETE CASCADE
);
-- This partial unique index applies only to open rows: the same conversation may
-- retain old closed tickets but cannot accumulate multiple simultaneously open ones.
CREATE UNIQUE INDEX one_open_ticket ON tickets(sandbox_id, customer_id, conv_id) WHERE status='open';
-- Events are append-style history entries written alongside state changes. They aid
-- troubleshooting but are neither tamper-proof nor the sole source of business state.
-- action_id is optional because a handoff event can refer to a ticket instead.
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, sandbox_id TEXT NOT NULL, customer_id TEXT NOT NULL,
    conv_id TEXT NOT NULL, action_id TEXT, kind TEXT NOT NULL, payload_json TEXT NOT NULL, at INTEGER NOT NULL,
    FOREIGN KEY (sandbox_id, customer_id) REFERENCES customers(sandbox_id, id) ON DELETE CASCADE
);
