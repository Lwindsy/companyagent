"""Provider boundary preserving native tool blocks and explicit usage accounting.

No environment is read here. The application injects an authenticated SDK client;
offline tests inject ScriptedModel, which cannot make an HTTP request.
"""
import json
import math
from typing import Protocol

from agent_runtime.context import SYSTEM, project_messages
from core.llm_utils import claude_request_kwargs
from observability.telemetry import llm_component


class ModelPort(Protocol):
    """One model turn returns native blocks, a stop reason and metered microdollars."""

                                                                            # Reading note:
                                                                            # The interface
                                                                            # promises an
                                                                            # integer number
                                                                            # of microdollars
                                                                            # to hold before
                                                                            # one request.
                                                                            # Implementations
                                                                            # receive
                                                                            # messages,
                                                                            # advertised
                                                                            # schemas and the
                                                                            # exact server
                                                                            # grant;
                                                                            # final_only
                                                                            # requests an
                                                                            # answer with no
                                                                            # further tool
                                                                            # calls. One
                                                                            # million
                                                                            # microdollars
                                                                            # equals one USD.
    def reservation(self, messages, tools, grant, final_only=False) -> int: ...
                                                                            # Reading note: An
                                                                            # async method is
                                                                            # awaited because
                                                                            # a provider may
                                                                            # need network
                                                                            # time.
                                                                            # Implementations
                                                                            # return native
                                                                            # content blocks,
                                                                            # stop_reason and
                                                                            # measured
                                                                            # cost/usage. This
                                                                            # declaration
                                                                            # supplies no
                                                                            # implementation
                                                                            # and cannot send
                                                                            # a request
                                                                            # itself.
    async def turn(self, messages, tools, grant, final_only=False) -> dict: ...


class AnthropicModel:
    """Use the existing SDK with no hidden repair loop or fallback.

    Prices are microdollars per token, numerically equal to USD per million tokens.
    The caller supplies dated prices; this adapter does not claim to verify invoices.
    An optional outer metering wrapper can enforce a shared experimental dollar cap.
    """

    def __init__(self, client, model, input_price=2, output_price=10):
        # Reading note: client is injected so this file never reads secrets. model is the
        # pinned provider model name. input_price and output_price use microdollars per token
        # (numerically USD per million tokens); reject zero, negative or nonfinite prices
        # before storing them. Prices are configuration estimates, not a verified invoice.
        if any(not math.isfinite(p) or p <= 0 for p in (input_price, output_price)):
            raise ValueError("Token prices must be positive finite values")
        self.client, self.model = client, model
        self.input_price, self.output_price = input_price, output_price

    def request(self, messages, tools, grant, final_only=False):
        # Native blocks (including provider continuation data) are copied intact.
        # Context projection only shortens old completed observations, never user text.
        # Reading note: messages is the complete native conversation, tools is the model-
        # facing schema list and grant is server authorization data. Produce a fresh request
        # dictionary, preserve continuation blocks, remove fallback settings and constrain
        # supported final JSON. final_only sets tool_choice to none. Returning request data
        # does not contact the provider; the turn method performs that step.
        projected, _ = project_messages(messages)
        kwargs = claude_request_kwargs(self.model, max_tokens=1024, temperature=0.0)
        extra = kwargs.setdefault("extra_body", {})
        extra.pop("fallbacks", None)
        if self.model.startswith("claude-sonnet-5-5"):
            # Constrain only the final answer's shape; the provider can still return
            # native tool_use blocks before that answer. Format is not permission.
            extra.setdefault("output_config", {})["format"] = {"type": "json_schema", "schema": {
                "type": "object", "additionalProperties": False,
                "properties": {"response": {"type": "string"},
                               "outcome": {"type": "string", "enum": ["resolved", "needs_input", "handoff"]},
                               "evidence_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["response", "outcome", "evidence_ids"]}}
        kwargs.get("extra_headers", {}).pop("anthropic-beta", None)
        if final_only:
            # A repeated observation is a stopping signal, not a reason to purchase
            # another tool loop. The provider must now answer from existing evidence.
            kwargs["tool_choice"] = {"type": "none"}
        return {**kwargs, "system": SYSTEM + "\nCURRENT SERVER GRANT: " + json.dumps(grant),
                "messages": projected, "tools": tools}

    def reservation(self, messages, tools, grant, final_only=False):
        # Reserve before transmission, including tool schemas and provider framing.
        # Unknown timeout retains this full amount. There is no monetary refund just
        # because the client stopped waiting for the provider's response.
        # Reading note: Build the same request that turn will send. Twice its UTF-8 byte
        # length plus 4096 estimates a conservative input bound; reserve the configured
        # maximum output as well. ceil rounds UP to whole microdollars. This is deliberately a
        # hold, not a tokenizer measurement, and an unknown timeout must retain it.
        request = self.request(messages, tools, grant, final_only)
        incoming_bound = len(json.dumps(request, ensure_ascii=False).encode()) * 2 + 4096
        return math.ceil(incoming_bound * self.input_price + request["max_tokens"] * self.output_price)

    async def turn(self, messages, tools, grant, final_only=False):
        # Reading note: Await exactly one SDK request under the agent.turn telemetry context.
        # Validate nonnegative integer token usage and the returned model family before
        # computing cost. Cache writes are conservatively charged at twice base input and
        # reads at full base input. Return native blocks and public usage; the engine saves
        # private continuation blocks separately from its public timeline. Transport/usage
        # errors propagate so no hidden retry is purchased.
        with llm_component("agent.turn"):
            reply = await self.client.messages.create(**self.request(messages, tools, grant, final_only))
        usage = reply.usage
        counts = [usage.input_tokens, usage.output_tokens,
                  getattr(usage, "cache_creation_input_tokens", 0) or 0,
                  getattr(usage, "cache_read_input_tokens", 0) or 0]
        if any(type(n) is not int or n < 0 for n in counts):
            raise ValueError("Invalid provider usage")
        if not reply.model.startswith(self.model):
            raise ValueError("Unexpected provider model")
        measured = math.ceil((counts[0] + 2 * counts[2] + counts[3]) * self.input_price + counts[1] * self.output_price)
        return {"content": [block.model_dump(exclude_none=True) for block in reply.content],
                "stop_reason": reply.stop_reason, "cost_micro": measured,
                "input_tokens": counts[0], "output_tokens": counts[1], "model": reply.model}


class ScriptedModel:
    """An explicit protocol test double, never evidence of real-model quality.

    decide receives only normal conversation/tool inputs. A function can inspect
    observations to exercise dynamic branches; the live adapter never uses it.
    """

    def __init__(self, decide):
        # Reading note: decide is a local test function; calls is a list used to inspect what
        # context it received. Keeping this explicit test double separate from AnthropicModel
        # prevents offline success from being mistaken for real-model quality.
        self.decide, self.calls = decide, []

    def reservation(self, messages, tools, grant, final_only=False):
        # Reading note: Offline reference functions perform no inference service call, so the
        # amount to reserve is exactly zero microdollars. Match the live interface including
        # final_only so the same engine can be exercised without credentials.
        return 0

    async def turn(self, messages, tools, grant, final_only=False):
        # Reading note: Copy messages into calls before invoking the injected reference
        # function with normal messages, schemas and grant. Return that function result
        # directly. It never receives hidden answer labels from this adapter and never makes
        # an SDK request; tests may deliberately return malformed results to exercise
        # failures.
        self.calls.append(json.loads(json.dumps(messages)))
        return self.decide(messages, tools, grant)
