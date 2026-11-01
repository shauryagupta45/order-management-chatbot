from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.query_audit import audited


def test_audit_logs_request_executed_query_and_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "q.log"
    monkeypatch.setenv("MCP_QUERY_LOG", str(log))
    monkeypatch.setenv("OSCO_QUERY_TAG", "last_year@2026-10-28")

    with audited("count_documents", database="osco", collection="orderHistory", filter={"a": 1}) as audit:
        audit.ran(filter={"$and": [{"a": 1}, {"isActive": True}]})
        audit.result = {"count": 42}

    text = log.read_text()
    assert "[last_year@2026-10-28]" in text and "count_documents osco.orderHistory  OK" in text
    assert 'EXECUTED: {"filter": {"$and": [{"a": 1}, {"isActive": true}]}}' in text
    assert 'RESPONSE: {"count": 42}' in text


def test_audit_logs_errors_and_hides_raw_documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "q.log"
    monkeypatch.setenv("MCP_QUERY_LOG", str(log))

    with pytest.raises(RuntimeError):
        with audited("find_documents", database="osco", collection="order", filter={}):
            raise RuntimeError("Data API POST /find returned HTTP 502")
    with audited("find_documents", database="osco", collection="order", filter={}, sanitize=False) as audit:
        audit.result = {"documents": [{"email": "x@y.z"}], "count_returned": 1}

    text = log.read_text()
    assert "ERROR RuntimeError" in text and "HTTP 502" in text
    assert "x@y.z" not in text and "raw documents not logged" in text
