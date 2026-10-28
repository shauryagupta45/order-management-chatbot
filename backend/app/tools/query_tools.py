from __future__ import annotations

from time import perf_counter
from typing import Any

from backend.app.dependencies import AppDependencies
from backend.app.services.projection_policy import ProjectionPolicy


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 2)


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
        documents = dependencies.repository.find(
            database=database,
            collection=collection,
            filter=normalized_filter,
            projection=applied_projection,
            sort=normalized_sort,
            limit=capped_limit,
        )
        formatted, warnings = dependencies.response_sanitizer.format_documents(schema, documents, sanitize=sanitize)
        return {
            "documents": formatted,
            "count_returned": len(formatted),
            "applied_projection": applied_projection,
            "limit": capped_limit,
            "sanitized": sanitize,
            "warnings": warnings,
            "elapsed_ms": _elapsed_ms(started_at),
        }

    @mcp.tool()
    def count_documents(database: str, collection: str, filter: dict[str, Any]) -> dict:
        """Use this for safe read-only MongoDB count queries against configured schema fields."""
        started_at = perf_counter()
        schema = dependencies.query_validator.get_schema(database, collection)
        dependencies.query_validator.validate_filter(schema, filter)
        normalized_filter = dependencies.query_validator.normalize_filter(schema, filter)
        return {"count": dependencies.repository.count(database, collection, normalized_filter), "elapsed_ms": _elapsed_ms(started_at)}

    @mcp.tool()
    def aggregate_documents(
        database: str, collection: str, pipeline: list[dict[str, Any]], limit: int = 10, sanitize: bool = True
    ) -> dict:
        """Use this for read-only aggregation when find/count cannot answer the question. By default PII/sensitive fields are stripped; pass sanitize=false only when raw documents are explicitly required."""
        started_at = perf_counter()
        schema = dependencies.query_validator.get_schema(database, collection)
        capped_limit = dependencies.query_validator.cap_limit(limit, schema.default_limit)
        dependencies.query_validator.validate_pipeline(schema, pipeline)
        documents = dependencies.repository.aggregate(database, collection, pipeline, capped_limit)
        formatted, warnings = dependencies.response_sanitizer.format_documents(schema, documents, sanitize=sanitize)
        return {
            "documents": formatted,
            "count_returned": len(formatted),
            "limit": capped_limit,
            "sanitized": sanitize,
            "warnings": warnings,
            "elapsed_ms": _elapsed_ms(started_at),
        }

    @mcp.tool()
    def explain_find_query(database: str, collection: str, filter: dict[str, Any]) -> dict:
        """Use this to inspect a safe find query plan and reason about index usage without returning documents."""
        started_at = perf_counter()
        schema = dependencies.query_validator.get_schema(database, collection)
        dependencies.query_validator.validate_filter(schema, filter)
        normalized_filter = dependencies.query_validator.normalize_filter(schema, filter)
        explain = dependencies.repository.explain_find(database, collection, normalized_filter)
        planner = explain.get("queryPlanner", {})
        return {
            "namespace": planner.get("namespace"),
            "winning_plan": planner.get("winningPlan"),
            "parsed_query": planner.get("parsedQuery"),
            "elapsed_ms": _elapsed_ms(started_at),
        }
