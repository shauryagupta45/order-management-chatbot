from __future__ import annotations

from time import perf_counter
from typing import Any

from backend.app.dependencies import AppDependencies
from backend.app.query_audit import audited
from backend.app.services.projection_policy import ProjectionPolicy


# Collections whose prod data is complete for its whole documented range, so an empty result is a real answer.
_PROD_ARCHIVE_COLLECTIONS = {("osco", "orderHistory")}

PROD_EMPTY_ARCHIVE_WARNING = (
    "Environment is prod, where osco.orderHistory holds every order back to at least December 2023. An empty result "
    "means this filter matched nothing, not that history data is missing. Check the filter (orderCreatedDate is a BSON "
    "date such as {\"$date\": \"2025-10-28T07:00:00Z\"}; only late-2023 records store an ISO string) before reporting "
    "zero orders."
)


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


def _environment_warnings(dependencies: AppDependencies, database: str, collection: str, returned: int) -> list[str]:
    if returned == 0 and dependencies.settings.environment == "prod" and (database, collection) in _PROD_ARCHIVE_COLLECTIONS:
        return [PROD_EMPTY_ARCHIVE_WARNING]
    return []


def register_query_tools(mcp, dependencies: AppDependencies) -> None:
    @mcp.tool()
    def find_documents(
        database: str,
        collection: str,
        filter: dict[str, Any],
        projection: dict[str, int | bool] | None = None,
        sort: dict[str, int | str] | None = None,
        limit: int = 10,
        sanitize: bool = True,
    ) -> dict:
        """Use this for read-only MongoDB find queries after resolving schema context. By default PII/sensitive fields are stripped; pass sanitize=false only when raw documents are explicitly required."""
        with audited("find_documents", database=database, collection=collection, filter=filter,
                     projection=projection, sort=sort, limit=limit, sanitize=sanitize) as audit:
            started_at = perf_counter()
            schema = dependencies.query_validator.get_schema(database, collection)
            capped_limit = dependencies.query_validator.cap_limit(limit, schema.default_limit)
            dependencies.query_validator.validate_filter(schema, filter)
            normalized_filter = dependencies.query_validator.normalize_filter(schema, filter)
            normalized_sort = dependencies.query_validator.validate_sort(schema, sort)
            if projection is None and not sanitize:
                applied_projection = None
            else:
                projection_policy = dependencies.projection_policy if sanitize else ProjectionPolicy(allow_pii_projection=True)
                applied_projection = projection_policy.build_projection(schema, projection)
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
            active_only_applied = _applies_active_only(database, collection, active_only, _mentions_is_active(filter))
            if active_only_applied:
                filter = {"$and": [filter, ACTIVE_FILTER]} if filter else dict(ACTIVE_FILTER)
            dependencies.query_validator.validate_filter(schema, filter)
            normalized_filter = dependencies.query_validator.normalize_filter(schema, filter)
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
        sanitize: bool = True,
        active_only: bool = True,
    ) -> dict:
        """Use this for read-only aggregation when find/count cannot answer the question. By default PII/sensitive fields are stripped; pass sanitize=false only when raw documents are explicitly required. For osco.order and osco.orderHistory, a leading {"$match": {"isActive": true}} is added by default so each order is counted once; pass active_only=false to aggregate over every version document (e.g. version history)."""
        with audited("aggregate_documents", database=database, collection=collection, pipeline=pipeline,
                     limit=limit, sanitize=sanitize, active_only=active_only) as audit:
            started_at = perf_counter()
            schema = dependencies.query_validator.get_schema(database, collection)
            capped_limit = dependencies.query_validator.cap_limit(limit, schema.default_limit)
            active_only_applied = _applies_active_only(database, collection, active_only, _mentions_is_active(pipeline))
            if active_only_applied:
                pipeline = [{"$match": dict(ACTIVE_FILTER)}, *pipeline]
            dependencies.query_validator.validate_pipeline(schema, pipeline)
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
            normalized_filter = dependencies.query_validator.normalize_filter(schema, filter)
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
