from __future__ import annotations

from backend.app.dependencies import AppDependencies, build_dependencies
from backend.app.logging_config import configure_logging
from backend.app.tools.diagnostic_tools import register_diagnostic_tools
from backend.app.tools.query_tools import register_query_tools
from backend.app.tools.schema_tools import register_schema_tools

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    FastMCP = None


def create_mcp_server(dependencies: AppDependencies | None = None):
    resolved_dependencies = dependencies or build_dependencies()
    configure_logging(resolved_dependencies.settings.mcp_log_level)
    if FastMCP is None:
        raise RuntimeError("The MCP Python SDK is not installed. Install dependencies from pyproject.toml.")
    mcp = FastMCP(resolved_dependencies.settings.mcp_server_name)
    register_diagnostic_tools(mcp, resolved_dependencies)
    register_schema_tools(mcp, resolved_dependencies)
    register_query_tools(mcp, resolved_dependencies)
    return mcp

