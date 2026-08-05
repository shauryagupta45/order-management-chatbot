"""
MCP Query Runner
----------------
A CLI for invoking MCP server tools from the terminal without editing this file.

Usage:
    python3 scripts/mcp_query.py <tool_name> '<json_kwargs>'

Examples:
    python3 scripts/mcp_query.py count_documents '{"database":"osco","collection":"order","filter":{"orderStatus":"CREATED"}}'
    python3 scripts/mcp_query.py find_documents '{"database":"osco","collection":"order","filter":{"orderNumber":"83151321"},"limit":1}'
    python3 scripts/mcp_query.py aggregate_documents '{"database":"osco","collection":"order","pipeline":[{"$addFields":{"_amt":{"$toDouble":"$orderTotal.amount"}}},{"$match":{"_amt":{"$gt":100}}},{"$group":{"_id":null,"count":{"$sum":1}}}],"limit":1}'
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any

sys.path.insert(0, ".")

from backend.app.dependencies import build_dependencies
from backend.app.tools.diagnostic_tools import register_diagnostic_tools
from backend.app.tools.query_tools import register_query_tools
from backend.app.tools.schema_tools import register_schema_tools


class McpRunner:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}
        deps = build_dependencies()
        register_diagnostic_tools(self, deps)
        register_schema_tools(self, deps)
        register_query_tools(self, deps)

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator

    def run(self, tool_name: str, **kwargs) -> dict:
        t0 = time.perf_counter()
        result = self.tools[tool_name](**kwargs)
        result["total_wall_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/mcp_query.py <tool_name> '<json_kwargs>'")
        sys.exit(1)

    tool_name = sys.argv[1]
    kwargs = json.loads(sys.argv[2])

    runner = McpRunner()
    if tool_name not in runner.tools:
        print(f"Unknown tool '{tool_name}'. Available: {list(runner.tools.keys())}")
        sys.exit(1)

    result = runner.run(tool_name, **kwargs)
    print(json.dumps(result, indent=2, default=str), flush=True)

