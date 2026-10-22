"""Read-only synthetic diagnostic service using the official MCP 2.x SDK.

Run as a module over stdio. Standard output belongs exclusively to JSON-RPC; no
credentials, network probes, shell tools or financial writes are exposed here.
"""
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel

from agent_runtime.tools import diagnostic


server = MCPServer("companyagent-synthetic-diagnostics", version="20261022")


class StatusResult(BaseModel):
    """Explicit output fields let MCP advertise and validate structured observations."""
    status: str
    incident_id: str | None
    evidence_id: str
    synthetic: bool


class IncidentResult(BaseModel):
    """Incident facts contain no callable instructions or customer identifiers."""
    incident_id: str
    cause: str
    status: str
    evidence_id: str
    synthetic: bool


@server.tool(structured_output=True)
def get_service_status() -> StatusResult:
    # Reading note: This no-argument read returns typed synthetic service status, including an
    # incident ID only when degraded. The server serializes its Pydantic result as structured
    # MCP output. It does not inspect a real monitoring system, access credentials or change
    # an account.
    """Read the synthetic status; degraded status identifies its one known incident."""
    return StatusResult.model_validate(diagnostic("get_service_status", {}))


@server.tool(structured_output=True)
def get_incident(incident_id: str) -> IncidentResult:
    # Reading note: incident_id must identify the known synthetic incident. Look up the
    # fixture and return a typed incident result with a source ID; an unknown ID raises a
    # protocol-visible error instead of inventing a cause. This tool has no write capability.
    """Read details for the incident ID returned by get_service_status."""
    return IncidentResult.model_validate(diagnostic("get_incident", {"incident_id": incident_id}))


if __name__ == "__main__":
    server.run(transport="stdio")
