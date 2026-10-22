"""Small domain contracts shared by adapters and workflows."""
from dataclasses import dataclass

# create immutable tuples of Clients and Sandboxes
@dataclass(frozen=True)
class Scope:
    # This is a named data object, not a tuple. frozen=True blocks ordinary field
    # reassignment after creation so an operation cannot accidentally change owners.
    # Together these two IDs identify one customer's data inside one demo sandbox.
    sandbox_id: str
    customer_id: str

# Business layer handles the error, later http layer can map to status codes and messages.
class BusinessError(Exception):
    # code is a stable identifier for programs; message explains the problem to people.
    # status is the HTTP-oriented numeric hint used by the adapter. This class imports
    # no web framework, so a command-line caller can catch it and ignore status.
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status

# Do not hot-load JSON.
# When there are few configurations, adding a dynamic configuration mechanism introduces additional issues such as validation and hot-update consistency.
@dataclass(frozen=True)
class PolicyConfig:
    # A dataclass builds the constructor for these named values. Callers can override
    # defaults when constructing it. frozen prevents later assignment, but neither
    # frozen nor the int annotations validate ranges or types at runtime.
    # Days/hours are durations; all monetary limits use integer euro cents.
    refund_window_days: int = 30
    # Automatic refunds also require a verified duplicate; this amount alone is insufficient.
    auto_refund_max_cents: int = 10000
    # Requests above this threshold require an approval note when approved.
    high_value_cents: int = 20000
    # The service measures a rolling 30-day total, not a calendar-month bucket.
    monthly_refund_cap_cents: int = 50000
    # Persisted action rows count toward this per-conversation allowance, including denials.
    max_writes_per_conversation: int = 6
    approval_ttl_hours: int = 24


@dataclass(frozen=True)
class Decision:
    # outcome names the next business state; reason is a machine-readable explanation.
    # Keeping this result separate from HTTP lets tests evaluate rules without a server.
    outcome: str
    reason: str


def refund_policy(*, age_seconds: int, amount_cents: int, refunded: bool,
                  pending_other: bool, monthly_total: int, verified_duplicate: bool,
                  approved: bool, config: PolicyConfig) -> Decision:
    """Pure rule evaluation. Facts must be loaded inside the execution transaction."""
    # This function reads no database and changes nothing. The service supplies facts
    # loaded under a transaction; otherwise two requests might check outdated values.
    if refunded or pending_other:
        return Decision("denied", "REFUND_ALREADY_REQUESTED")
    # 86400 is the number of seconds in a day. A future-dated charge is invalid too.
    if age_seconds < 0 or age_seconds > config.refund_window_days * 86400:
        return Decision("denied", "REFUND_WINDOW_EXPIRED")
    if amount_cents <= 0:
        return Decision("denied", "INVALID_AMOUNT")
    # Add the proposed refund before checking the cap; amounts remain integer cents.
    if monthly_total + amount_cents > config.monthly_refund_cap_cents:
        return Decision("denied", "REFUND_CAP_REACHED")
    # Approval is checked AFTER the hard rules. A supervisor cannot bypass a duplicate,
    # expired window, invalid amount, or the rolling account limit.
    if approved:
        return Decision("executed", "SUPERVISOR_APPROVED")
    if verified_duplicate and amount_cents <= config.auto_refund_max_cents:
        return Decision("executed", "VERIFIED_DUPLICATE")
    # pending means review is required; no refund has occurred at this stage.
    return Decision("pending", "HIGH_VALUE_REVIEW" if amount_cents > config.high_value_cents else "REFUND_REVIEW")
