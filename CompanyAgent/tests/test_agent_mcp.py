"""Actual official MCP SDK client/server subprocess integration, no external API."""
from types import SimpleNamespace

import pytest

from agent_runtime.mcp_client import DiagnosticClient


async def test_real_stdio_discovery_and_structured_read():
    client = DiagnosticClient()
    status = await client.call("get_service_status", {})
    assert status["synthetic"] and status["incident_id"] == "incident-demo-1"
    incident = await client.call("get_incident", {"incident_id": status["incident_id"]})
    assert incident["evidence_id"] == "incident:demo-1"
    with pytest.raises(ValueError):
        await client.call("request_refund", {"charge_id": "ch_1"})


async def test_mcp_rejects_unknown_incident_without_a_fabricated_success():
    # The child returns a protocol error, never a guessed incident. Python 3.12
    # task groups may wrap the transport failure in an ExceptionGroup.
    with pytest.raises((ConnectionError, ExceptionGroup)):
        await DiagnosticClient().call('get_incident',{'incident_id':'unknown'})


@pytest.mark.parametrize("failure", ["capability", "schema", "timeout", "disconnect"])
async def test_mcp_faults_close_the_connection_without_returning_evidence(monkeypatch, failure):
    """Inject four boundary failures without waiting for a real eight-second timer.

    monkeypatch temporarily replaces the SDK connection constructor, and pytest
    restores it after each case. The two tests above exercise the real transport;
    this test checks our client's behavior when that transport reports a fault.
    A missing return value matters: callers must not receive invented evidence
    after a timeout, broken connection or changed server capability.
    """
    closed = []

    class Connection:
        """Minimal async context manager with the same methods used by our client."""

        def __init__(self, parameters, read_timeout_seconds):
            # The subprocess receives only operating-system essentials. No model
            # credential is forwarded; these assertions inspect names, not secrets.
            assert "ANTHROPIC_API_KEY" not in parameters.env
            assert parameters.args == ["-m", "agent_runtime.mcp_server"]
            assert read_timeout_seconds == 8

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            # async with must release the connection on success AND exceptions.
            # Recording exit lets the assertion detect a leaked connection.
            closed.append(True)

        async def list_tools(self):
            tools = [SimpleNamespace(name="get_service_status", input_schema={"properties": {}}),
                     SimpleNamespace(name="get_incident", input_schema={"properties": {"incident_id": {}}})]
            if failure == "capability":
                tools.append(SimpleNamespace(name="request_refund", input_schema={}))
            if failure == "schema":
                tools[0].input_schema["properties"]["customer_id"] = {}
            return SimpleNamespace(tools=tools)

        async def call_tool(self, *_):
            # Raising represents an SDK-reported timeout/disconnect. It does not
            # pretend that a synthetic connection exercised an actual wire fault.
            if failure == "timeout":
                raise TimeoutError("injected transport timeout")
            raise ConnectionError("injected transport disconnect")

    monkeypatch.setattr("agent_runtime.mcp_client.Client", Connection)
    expected = ValueError if failure in {"capability", "schema"} else TimeoutError if failure == "timeout" else ConnectionError
    with pytest.raises(expected):
        await DiagnosticClient().call("get_service_status", {})
    assert closed == [True]
