"""A fixed, credential-free stdio MCP integration with an exact tool allowlist."""
import os
from pathlib import Path
import sys

from mcp import Client
from mcp.client.stdio import StdioServerParameters


class DiagnosticClient:
    """Open a short-lived official client/server connection for diagnostic reads.

    A connection per read keeps subprocess ownership simple across cancellation.
    The allowlist rejects tool-list changes rather than silently adding capability.
    """

    async def call(self, name, arguments):
        # Reading note: name is one of exactly two read-only diagnostic tools; arguments is
        # its validated JSON object. Build a fixed Python child command and a small OS-only
        # environment, then enter the official SDK async context so success, cancellation and
        # errors close the child. Discover names and parameter keys before calling. Return
        # structured data only; changed capabilities, protocol errors and missing structured
        # output raise instead of becoming evidence. Eight seconds bounds SDK reads and the
        # gateway adds an outer deadline.
        if name not in {"get_service_status", "get_incident"}:
            raise ValueError("MCP tool is not allowed")
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH") if key in os.environ}
        env["PYTHONUTF8"] = "1"
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "agent_runtime.mcp_server"], env=env)
        async with Client(parameters, read_timeout_seconds=8) as client:
            tools = (await client.list_tools()).tools
            if {tool.name for tool in tools} != {"get_service_status", "get_incident"}:
                raise ValueError("MCP tool capabilities changed")
            # Validate advertised parameter keys as well as tool names. An altered
            # server cannot introduce a customer/credential parameter unnoticed.
            for tool in tools:
                expected = {"incident_id"} if tool.name == "get_incident" else set()
                if set(tool.input_schema.get("properties", {})) != expected:
                    raise ValueError("MCP tool schema changed")
            reply = await client.call_tool(name, arguments)
            if reply.is_error or reply.structured_content is None:
                raise ConnectionError("MCP read did not return structured data")
            return reply.structured_content
