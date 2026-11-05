from __future__ import annotations

import calendar
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

from backend.app.dependencies import AppDependencies
from backend.app.domain.errors import DataApiError, ValidationError
from backend.app.domain.schema_models import CollectionSchema
from backend.app.query_audit import audited
from backend.app.services.date_filters import expand_mixed_date_filter, expand_mixed_date_pipeline, parse_instant
from backend.app.services.projection_policy import ProjectionPolicy


# Collections whose prod data is complete for its whole documented range, so an empty result is a real answer.
_PROD_ARCHIVE_COLLECTIONS = {("osco", "orderHistory")}

PROD_EMPTY_ARCHIVE_WARNING = (
    "Environment is prod, where osco.orderHistory holds the last 2 years of orders. An empty result means this filter "
    "matched nothing, not that history data is missing. Check the filter (date range, day boundaries, field values) "
    "before reporting zero orders. Date fields already match both BSON dates and ISO strings."
)

# osco.orderHistory is a prod-only archive: in nonprod it is never queried and reads as empty.
_PROD_ONLY_COLLECTIONS = {("osco", "orderHistory")}
NONPROD_SKIPPED_NOTE = "osco.orderHistory is not queried in nonprod; it always returns 0. Use osco.order instead."

# Prod: orders are moved from osco.order to osco.orderHistory in batches, so the boundary is wherever orderHistory
# currently ends (its newest orderCreatedDate), not a fixed age. ORDER_RETENTION_MONTHS is only the fallback when that
# lookup fails. HISTORY_RETENTION_MONTHS is the range orderHistory is relied on for; older dates are still queried.
ORDER_RETENTION_MONTHS = 3
HISTORY_RETENTION_MONTHS = 24
# Confirmed rule: dates a year old or older ("same day last year") always come from osco.orderHistory, so the split
# point is never earlier than this, whatever the handoff lookup returns.
HISTORY_ALWAYS_MONTHS = 12
# Without an {orderCreatedDate, isActive} index every order version in a window is fetched to check isActive, so a
# busy full day times out while a 3-hour window answers in about a second. Ranges start as 3-hour windows; longer
# ranges use bigger windows so the number of queries stays bounded.
COUNT_ORDERS_WINDOW = timedelta(hours=3)
COUNT_ORDERS_MAX_WINDOWS = 96
# A window that fails is split in half and retried, down to this size.
COUNT_ORDERS_MIN_WINDOW = timedelta(hours=1)
# MongoDB stops a window query after this instead of running on after the client gives up. Kept just under the
# 60 s client timeout: a day nobody has queried recently is read from disk and needs well over 20 s per window.
COUNT_ORDERS_MAX_TIME_MS = 50_000
# Count queries in flight against MongoDB at once, across all tool calls in this process.
COUNT_ORDERS_PARALLELISM = 2
_count_slots = threading.BoundedSemaphore(COUNT_ORDERS_PARALLELISM)


# Collections that keep several versions of one order; exactly one version per orderNumber has isActive=true, so
# counts and aggregations filter on it by default to count each order once.
_VERSIONED_ORDER_COLLECTIONS = {("osco", "order"), ("osco", "orderHistory")}
ACTIVE_FILTER = {"isActive": True}


def _applies_active_only(database: str, collection: str, active_only: bool, mentions_is_active: bool) -> bool:
    # An explicit isActive condition from the caller wins over the default.
    return active_only and not mentions_is_active and (database, collection) in _VERSIONED_ORDER_COLLECTIONS


def _mentions_is_active(value: Any) -> bool:
    if isinstance(value, dict):
        return "isActive" in value or any(_mentions_is_active(item) for item in value.values())
    if isinstance(value, list):
        return any(_mentions_is_active(item) for item in value)
    return False


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _months_before(instant: datetime, months: int) -> datetime:
    years, month_index = divmod(instant.month - 1 - months, 12)
    year, month = instant.year + years, month_index + 1
    return instant.replace(year=year, month=month, day=min(instant.day, calendar.monthrange(year, month)[1]))


def _iso(instant: datetime) -> str:
    return instant.isoformat(timespec="milliseconds" if instant.microsecond else "seconds").replace("+00:00", "Z")


def _skipped_in_nonprod(dependencies: AppDependencies, database: str, collection: str) -> bool:
    return dependencies.settings.environment == "nonprod" and (database, collection) in _PROD_ONLY_COLLECTIONS


def _prepare_count_filter(
    dependencies: AppDependencies, schema: CollectionSchema, filter: dict[str, Any], active_only: bool
) -> tuple[dict[str, Any], bool]:
    """Validate a count filter and return it as sent to the database: isActive default plus mixed date/string dates."""
    active_only_applied = _applies_active_only(schema.database, schema.collection, active_only, _mentions_is_active(filter))
    if active_only_applied:
        filter = {"$and": [filter, ACTIVE_FILTER]} if filter else dict(ACTIVE_FILTER)
    dependencies.query_validator.validate_filter(schema, filter)
    normalized = dependencies.query_validator.normalize_filter(schema, filter)
    return expand_mixed_date_filter(schema, normalized), active_only_applied


def _environment_warnings(dependencies: AppDependencies, database: str, collection: str, returned: int) -> list[str]:
    if returned == 0 and dependencies.settings.environment == "prod" and (database, collection) in _PROD_ARCHIVE_COLLECTIONS:
        return [PROD_EMPTY_ARCHIVE_WARNING]
    return []


def _skipped_result(dependencies: AppDependencies, started_at: float, **empty: Any) -> dict[str, Any]:
    return {
        **empty,
        "skipped": True,
        "environment": dependencies.settings.environment,
        "warnings": [NONPROD_SKIPPED_NOTE],
        "elapsed_ms": _elapsed_ms(started_at),
    }


def register_query_tools(mcp, dependencies: AppDependencies) -> None:
    @mcp.tool()
    def find_documents(
        database: str,
        collection: str,
        filter: dict[str, Any],
        projection: dict[str, int | bool] | None = None,
        sort: dict[str, int | str] | None = None,
        limit: int = 10,
    ) -> dict:
        """Use this for read-only MongoDB find queries after resolving schema context. PII/sensitive fields are stripped unless the collection's schema disables sanitization."""
        with audited("find_documents", database=database, collection=collection, filter=filter,
                     projection=projection, sort=sort, limit=limit) as audit:
            started_at = perf_counter()
            schema = dependencies.query_validator.get_schema(database, collection)
            sanitize = schema.sanitize
            capped_limit = dependencies.query_validator.cap_limit(limit, schema.default_limit)
            dependencies.query_validator.validate_filter(schema, filter)
            normalized_filter = expand_mixed_date_filter(
                schema, dependencies.query_validator.normalize_filter(schema, filter)
            )
            normalized_sort = dependencies.query_validator.validate_sort(schema, sort)
            if projection is None and not sanitize:
                applied_projection = None
            else:
                projection_policy = dependencies.projection_policy if sanitize else ProjectionPolicy(allow_pii_projection=True)
                applied_projection = projection_policy.build_projection(schema, projection)
            if _skipped_in_nonprod(dependencies, database, collection):
                audit.result = _skipped_result(dependencies, started_at, documents=[], count_returned=0)
                return audit.result
            audit.ran(filter=normalized_filter, projection=applied_projection, sort=normalized_sort, limit=capped_limit)
            documents = dependencies.repository.find(
                database=database,
                collection=collection,
                filter=normalized_filter,
                projection=applied_projection,
                sort=normalized_sort,
                limit=capped_limit,
            )
            formatted, warnings = dependencies.response_sanitizer.format_documents(schema, documents, sanitize=sanitize)
            warnings += _environment_warnings(dependencies, database, collection, len(formatted))
            audit.result = {
                "documents": formatted,
                "count_returned": len(formatted),
                "environment": dependencies.settings.environment,
                "applied_projection": applied_projection,
                "limit": capped_limit,
                "sanitized": sanitize,
                "warnings": warnings,
                "elapsed_ms": _elapsed_ms(started_at),
            }
            return audit.result

    @mcp.tool()
    def count_documents(database: str, collection: str, filter: dict[str, Any], active_only: bool = True) -> dict:
        """Use this for safe read-only MongoDB count queries against configured schema fields. For osco.order and osco.orderHistory, isActive=true is added by default so each order is counted once (one active version per order); pass active_only=false to count every version document."""
        with audited("count_documents", database=database, collection=collection, filter=filter,
                     active_only=active_only) as audit:
            started_at = perf_counter()
            schema = dependencies.query_validator.get_schema(database, collection)
            normalized_filter, active_only_applied = _prepare_count_filter(dependencies, schema, filter, active_only)
            if _skipped_in_nonprod(dependencies, database, collection):
                audit.result = _skipped_result(dependencies, started_at, count=0)
                return audit.result
            audit.ran(filter=normalized_filter)
            total = dependencies.repository.count(database, collection, normalized_filter)
            audit.result = {
                "count": total,
                "active_only_applied": active_only_applied,
                "environment": dependencies.settings.environment,
                "warnings": _environment_warnings(dependencies, database, collection, total),
                "elapsed_ms": _elapsed_ms(started_at),
            }
            return audit.result

    @mcp.tool()
    def aggregate_documents(
        database: str,
        collection: str,
        pipeline: list[dict[str, Any]],
        limit: int = 10,
        active_only: bool = True,
    ) -> dict:
        """Use this for read-only aggregation when find/count cannot answer the question. PII/sensitive fields are stripped unless the collection's schema disables sanitization. For osco.order and osco.orderHistory, a leading {"$match": {"isActive": true}} is added by default so each order is counted once; pass active_only=false to aggregate over every version document (e.g. version history)."""
        with audited("aggregate_documents", database=database, collection=collection, pipeline=pipeline,
                     limit=limit, active_only=active_only) as audit:
            started_at = perf_counter()
            schema = dependencies.query_validator.get_schema(database, collection)
            sanitize = schema.sanitize
            capped_limit = dependencies.query_validator.cap_limit(limit, schema.default_limit)
            active_only_applied = _applies_active_only(database, collection, active_only, _mentions_is_active(pipeline))
            if active_only_applied:
                pipeline = [{"$match": dict(ACTIVE_FILTER)}, *pipeline]
            dependencies.query_validator.validate_pipeline(schema, pipeline)
            pipeline = dependencies.projection_policy.secure_pipeline(schema, expand_mixed_date_pipeline(schema, pipeline))
            if _skipped_in_nonprod(dependencies, database, collection):
                audit.result = _skipped_result(dependencies, started_at, documents=[], count_returned=0)
                return audit.result
            audit.ran(pipeline=pipeline, limit=capped_limit)
            documents = dependencies.repository.aggregate(database, collection, pipeline, capped_limit)
            formatted, warnings = dependencies.response_sanitizer.format_documents(schema, documents, sanitize=sanitize)
            warnings += _environment_warnings(dependencies, database, collection, len(formatted))
            audit.result = {
                "documents": formatted,
                "count_returned": len(formatted),
                "active_only_applied": active_only_applied,
                "environment": dependencies.settings.environment,
                "limit": capped_limit,
                "sanitized": sanitize,
                "warnings": warnings,
                "elapsed_ms": _elapsed_ms(started_at),
            }
            return audit.result

    @mcp.tool()
    def explain_find_query(database: str, collection: str, filter: dict[str, Any]) -> dict:
        """Use this to inspect a safe find query plan and reason about index usage without returning documents."""
        with audited("explain_find_query", database=database, collection=collection, filter=filter) as audit:
            started_at = perf_counter()
            schema = dependencies.query_validator.get_schema(database, collection)
            dependencies.query_validator.validate_filter(schema, filter)
            normalized_filter = expand_mixed_date_filter(
                schema, dependencies.query_validator.normalize_filter(schema, filter)
            )
            audit.ran(filter=normalized_filter)
            explain = dependencies.repository.explain_find(database, collection, normalized_filter)
            planner = explain.get("queryPlanner", {})
            audit.result = {
                "namespace": planner.get("namespace"),
                "winning_plan": planner.get("winningPlan"),
                "parsed_query": planner.get("parsedQuery"),
                "elapsed_ms": _elapsed_ms(started_at),
            }
            return audit.result

    def _history_handoff(now: datetime) -> datetime | None:
        """Instant just after the newest order in osco.orderHistory: older orders are counted there, newer in osco.order."""
        try:
            documents = dependencies.repository.find(
                database="osco",
                collection="orderHistory",
                filter={"orderCreatedDate": {"$lte": now}},
                projection={"orderCreatedDate": 1},
                sort=[("orderCreatedDate", -1)],
                limit=1,
            )
        except DataApiError:
            return None
        newest = parse_instant(documents[0].get("orderCreatedDate")) if documents else None
        return newest + timedelta(milliseconds=1) if newest else None

    def _count_window(schema: CollectionSchema, filter: dict[str, Any], start: datetime, end: datetime) -> tuple[int, int]:
        """Count one [start, end) window; on a data API failure (usually a timeout) split it in half and retry."""
        window = {"orderCreatedDate": {"$gte": start, "$lt": end}}
        combined = {"$and": [filter, window]} if filter else window
        prepared, _ = _prepare_count_filter(dependencies, schema, combined, active_only=True)
        try:
            with _count_slots:
                count = dependencies.repository.count(
                    schema.database, schema.collection, prepared, max_time_ms=COUNT_ORDERS_MAX_TIME_MS
                )
            return count, 1
        except DataApiError:
            if end - start <= COUNT_ORDERS_MIN_WINDOW:
                raise
            middle = start + (end - start) / 2
            left, left_queries = _count_window(schema, filter, start, middle)
            right, right_queries = _count_window(schema, filter, middle, end)
            return left + right, left_queries + right_queries

    def _count_range(schema: CollectionSchema, filter: dict[str, Any], start: datetime, end: datetime) -> tuple[int, int]:
        size = max(COUNT_ORDERS_WINDOW, (end - start) / COUNT_ORDERS_MAX_WINDOWS)
        chunks, cursor = [], start
        while cursor < end:
            chunk_end = min(cursor + size, end)
            chunks.append((cursor, chunk_end))
            cursor = chunk_end
        with ThreadPoolExecutor(max_workers=COUNT_ORDERS_PARALLELISM) as pool:
            results = list(pool.map(lambda chunk: _count_window(schema, filter, *chunk), chunks))
        return sum(count for count, _ in results), sum(queries for _, queries in results)

    @mcp.tool()
    def count_orders(start: str, end: str, filter: dict[str, Any] | None = None) -> dict:
        """Count OSCO orders created in [start, end), each order once (isActive=true). Prefer this over count_documents for any order count by date. start/end are ISO-8601 UTC instants of the business-day boundaries, e.g. "2025-10-28T05:00:00Z". filter adds extra conditions (banner, storeNumber, orderStatus, ...). In prod the range is split automatically: the last 3 months come from osco.order, older dates (up to 2 years back) from osco.orderHistory, and the parts are added. In nonprod only osco.order is queried. Long ranges are counted in chunks so they do not time out."""
        extra_filter = filter or {}
        with audited("count_orders", database="osco", collection="order+orderHistory", start=start, end=end,
                     filter=extra_filter) as audit:
            started_at = perf_counter()
            range_start, range_end = parse_instant(start), parse_instant(end)
            if range_start is None or range_end is None:
                raise ValidationError('start and end must be ISO-8601 instants, e.g. "2025-10-28T05:00:00Z".')
            if range_end <= range_start:
                raise ValidationError("end must be after start.")
            environment = dependencies.settings.environment
            now = _now()
            history_floor = _months_before(now, HISTORY_RETENTION_MONTHS)
            order_cutoff, cutoff_source = _months_before(now, ORDER_RETENTION_MONTHS), "fallback: 3 months before now"

            plan: list[tuple[str, datetime, datetime]] = []
            warnings: list[str] = []
            if environment == "nonprod":
                plan.append(("order", range_start, range_end))
                warnings.append(NONPROD_SKIPPED_NOTE)
            else:
                handoff = _history_handoff(now)
                if handoff is not None:
                    order_cutoff, cutoff_source = handoff, "newest orderCreatedDate in osco.orderHistory"
                else:
                    warnings.append("Could not read where osco.orderHistory ends; split at 3 months before now instead.")
                history_always = _months_before(now, HISTORY_ALWAYS_MONTHS)
                if order_cutoff < history_always:
                    order_cutoff, cutoff_source = history_always, f"{HISTORY_ALWAYS_MONTHS} months before now (last-year rule)"
                if range_end > order_cutoff:
                    plan.append(("order", max(range_start, order_cutoff), range_end))
                if range_start < order_cutoff:
                    plan.append(("orderHistory", range_start, min(range_end, order_cutoff)))
                if range_start < history_floor:
                    warnings.append(
                        f"The range starts more than {HISTORY_RETENTION_MONTHS} months back (before "
                        f"{_iso(history_floor)}); osco.orderHistory has records from 2019, but completeness that far "
                        "back is not verified."
                    )

            for collection, _, _ in plan:
                dependencies.query_validator.validate_filter(
                    dependencies.query_validator.get_schema("osco", collection), extra_filter
                )
            audit.ran(plan=[{"collection": f"osco.{c}", "from": _iso(s), "to": _iso(e)} for c, s, e in plan],
                      filter=extra_filter, order_cutoff=_iso(order_cutoff), cutoff_source=cutoff_source)
            parts = []
            for collection, part_start, part_end in plan:
                schema = dependencies.query_validator.get_schema("osco", collection)
                count, queries = _count_range(schema, extra_filter, part_start, part_end)
                parts.append({
                    "collection": f"osco.{collection}",
                    "from": _iso(part_start),
                    "to": _iso(part_end),
                    "count": count,
                    "queries": queries,
                })
            total = sum(part["count"] for part in parts)
            if total == 0 and environment == "prod" and any(p["collection"] == "osco.orderHistory" for p in parts):
                warnings.append(PROD_EMPTY_ARCHIVE_WARNING)
            audit.result = {
                "total": total,
                "environment": environment,
                "order_cutoff": _iso(order_cutoff),
                "cutoff_source": cutoff_source,
                "parts": parts,
                "warnings": warnings,
                "elapsed_ms": _elapsed_ms(started_at),
            }
            return audit.result
