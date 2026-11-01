from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_query_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests run real tool code against fake repositories; keep their audit entries out of logs/mcp_queries.log.
    monkeypatch.setenv("MCP_QUERY_LOG", str(tmp_path / "mcp_queries.log"))
    monkeypatch.delenv("OSCO_QUERY_TAG", raising=False)
