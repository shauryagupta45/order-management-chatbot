from __future__ import annotations

from backend.app.dependencies import AppDependencies


def register_diagnostic_tools(mcp, dependencies: AppDependencies) -> None:
    @mcp.tool()
    def health_check() -> dict:
        """Use this to verify that the MCP server process is alive and has loaded schema resources, and to see which environment (prod/nonprod) the data comes from."""
        return {
            "ok": True,
            "server": dependencies.settings.mcp_server_name,
            "environment": dependencies.settings.environment,
            "read_only": dependencies.settings.mcp_read_only,
            "configured_databases": [database.name for database in dependencies.catalog.list_databases()],
        }

    @mcp.tool()
    def mongodb_ping() -> dict:
        """Use this to verify the data source (data API or MongoDB, per DATA_ACCESS_MODE) is reachable without exposing connection details."""
        try:
            dependencies.repository.ping()
            return {"ok": True}
        except Exception as exc:  # pragma: no cover - needs a live MongoDB failure
            return {"ok": False, "error": f"Data source ping failed: {exc.__class__.__name__}"}

