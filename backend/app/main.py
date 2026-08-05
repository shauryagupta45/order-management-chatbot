from __future__ import annotations

from backend.app.mcp_server import create_mcp_server

mcp = create_mcp_server()

if __name__ == "__main__":
    mcp.run(transport="stdio")
