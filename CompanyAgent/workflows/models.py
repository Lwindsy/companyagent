from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


# This Pydantic model checks the shape of extracted data at runtime. Literal lists
# the only allowed branch names; it does not prove the model understood the customer.
class ExtractedPlan(BaseModel):
    # Reject extra identity/approval fields and implicit conversions such as "true" to True.
    model_config = ConfigDict(extra="forbid", strict=True)
    intent: Literal["charges", "refund", "renewal", "status", "handoff", "clarify"]
    # None means unknown, so the workflow must resolve an unambiguous target or ask.
    charge_id: str | None = Field(default=None, max_length=80)
    reason_code: Literal["duplicate_charge", "other"] = "other"
    # True cancels renewal, False restores it, None requires a clarification question.
    cancel_at_period_end: bool | None = None


class Extractor(Protocol):
    # A Protocol describes a required method without choosing its implementation.
    # async/await permits waiting for network I/O; tests supply a local fake instead.
    # recent is language context, overview is account reference data, and the returned
    # plan is a suggestion that must still pass trusted workflow and business rules.
    async def extract(self, message: str, recent: list[dict], overview: dict) -> ExtractedPlan: ...
