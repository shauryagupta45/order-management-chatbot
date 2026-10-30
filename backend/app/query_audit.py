"""Audit log of every query tool call: what was asked, the exact query that ran, and what came back.

The MCP server talks stdio, so its stderr is hidden inside the MCP client. This writes one readable block per tool
call to logs/mcp_queries.log (override with MCP_QUERY_LOG) so you can see how an answer was calculated.

Set OSCO_QUERY_TAG in the client's environment (the order-insights dashboard sets it to the question id) to label
which question a query belongs to.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator
from zoneinfo import ZoneInfo

logger = logging.getLogger("query_audit")

DEFAULT_LOG = Path(__file__).resolve().parents[2] / "logs" / "mcp_queries.log"
MAX_DOCS_LOGGED = 10
MAX_RESPONSE_CHARS = 6000
_lock = threading.Lock()
IST = ZoneInfo("Asia/Kolkata")  # log timestamps are in IST


def _log_path() -> Path:
    return Path(os.environ.get("MCP_QUERY_LOG") or DEFAULT_LOG)


def _dump(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _summarize_response(result: dict[str, Any], sanitized: bool) -> dict[str, Any]:
    summary = {k: v for k, v in result.items() if k != "documents"}
    if "documents" in result:
        docs = result["documents"] or []
        if sanitized:
            summary["documents"] = docs[:MAX_DOCS_LOGGED]
            if len(docs) > MAX_DOCS_LOGGED:
                summary["documents_not_logged"] = len(docs) - MAX_DOCS_LOGGED
        else:
            # Raw (unsanitized) documents can contain PII - log their shape only.
            summary["documents"] = f"<{len(docs)} raw documents not logged (sanitize=false)>"
    return summary


class AuditEntry:
    def __init__(self, tool: str, request: dict[str, Any]) -> None:
        self.tool = tool
        self.request = request
        self.effective: dict[str, Any] = {}
        self.result: dict[str, Any] | None = None

    def ran(self, **effective_query: Any) -> None:
        """Record the query exactly as sent to the database (after validation and default filters)."""
        self.effective.update(effective_query)


@contextmanager
def audited(tool: str, **request: Any) -> Iterator[AuditEntry]:
    entry = AuditEntry(tool, request)
    started = perf_counter()
    error: BaseException | None = None
    try:
        yield entry
    except BaseException as exc:
        error = exc
        raise
    finally:
        _write(entry, error, round((perf_counter() - started) * 1000, 1))


def _write(entry: AuditEntry, error: BaseException | None, elapsed_ms: float) -> None:
    req = entry.request
    target = f"{req.get('database')}.{req.get('collection')}"
    tag = os.environ.get("OSCO_QUERY_TAG") or "-"
    status = f"ERROR {error.__class__.__name__}" if error else "OK"
    lines = [
        f"===== {datetime.now(IST):%Y-%m-%d %H:%M:%S} IST  [{tag}]  pid={os.getpid()}  "
        f"{entry.tool} {target}  {status}  {elapsed_ms} ms",
        f"REQUEST:  {_dump({k: v for k, v in req.items() if k not in ('database', 'collection')})}",
    ]
    if entry.effective:
        lines.append(f"EXECUTED: {_dump(entry.effective)}")
    if error:
        lines.append(f"ERROR:    {str(error)[:2000]}")
    elif entry.result is not None:
        text = _dump(_summarize_response(entry.result, req.get("sanitize", True) is not False))
        if len(text) > MAX_RESPONSE_CHARS:
            text = text[:MAX_RESPONSE_CHARS] + f"... <truncated, {len(text)} chars>"
        lines.append(f"RESPONSE: {text}")
    block = "\n".join(lines) + "\n"

    logger.info("%s %s %s [%s] %sms", entry.tool, target, status, tag, elapsed_ms)
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a", encoding="utf-8") as fh:
            fh.write(block)
    except OSError as exc:  # logging must never break a query
        logger.warning("could not write query audit log: %s", exc)
