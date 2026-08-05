from __future__ import annotations

from backend.app.dependencies import AppDependencies


def register_diagnostic_tools(mcp, dependencies: AppDependencies) -> None:
    @mcp.tool()
    def health_check() -> dict:
        """Use this to verify that the MCP server process is alive and has loaded schema resources."""
        return {
            "ok": True,
            "server": dependencies.settings.mcp_server_name,
            "read_only": dependencies.settings.mcp_read_only,
            "configured_databases": [database.name for database in dependencies.catalog.list_databases()],
        }

    @mcp.tool()
    def mongodb_ping() -> dict:
        """Use this to verify MongoDB connectivity without exposing the MongoDB connection string."""
        try:
            dependencies.repository.ping()
            return {"ok": True}
        except Exception as exc:  # pragma: no cover - needs a live MongoDB failure
            return {"ok": False, "error": f"MongoDB ping failed: {exc.__class__.__name__}"}

