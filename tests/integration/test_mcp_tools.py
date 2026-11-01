from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.catalog.yaml_schema_catalog import YamlSchemaCatalog
from backend.app.config import LocalTestSettings
from backend.app.dependencies import AppDependencies
from backend.app.services.projection_policy import ProjectionPolicy
from backend.app.services.query_builder import QueryBuilder
from backend.app.services.query_validator import QueryValidator
from backend.app.services.response_sanitizer import ResponseSanitizer
from backend.app.services.target_resolver import TargetResolver
from backend.app.domain.errors import DataApiError
from backend.app.tools import query_tools
from backend.app.tools.query_tools import NONPROD_SKIPPED_NOTE, PROD_EMPTY_ARCHIVE_WARNING, register_query_tools


class FakeMcp:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self):
        def decorator(function):
            self.tools[function.__name__] = function
            return function

        return decorator


class FakeRepository:
    def __init__(self) -> None:
        self.last_projection = None

    def ping(self) -> bool:
        return True

    def find(self, database, collection, filter, projection, sort, limit):
        self.last_projection = projection
        return [
            {
                "_id": "507f1f77bcf86cd799439011",
                "extra": "not projected by fake repo",
                "customer": {"email": [{"_id": "person@example.com", "type": "PERSONAL"}]},
            }
        ]

    def count(self, database, collection, filter):
        return 1

    def aggregate(self, database, collection, pipeline, limit):
        return [{"status": "PAID", "total": 1}]

    def explain_find(self, database, collection, filter):
        return {"queryPlanner": {"namespace": f"{database}.{collection}", "winningPlan": {"stage": "IXSCAN"}}}


def build_test_dependencies(repository: FakeRepository) -> AppDependencies:
    settings = LocalTestSettings(MCP_SCHEMA_RESOURCE_DIR=Path("backend/app/resources"))
    catalog = YamlSchemaCatalog(settings.mcp_schema_resource_dir)
    return AppDependencies(
        settings=settings,
        catalog=catalog,
        mongo_client_provider=None,
        repository=repository,
        target_resolver=TargetResolver(catalog),
        query_validator=QueryValidator(catalog, settings.mcp_max_documents_per_query),
        projection_policy=ProjectionPolicy(),
        response_sanitizer=ResponseSanitizer(settings.mcp_max_bytes_per_response),
        query_builder=QueryBuilder(),
    )


def test_find_documents_tool_returns_raw_by_default() -> None:
    repository = FakeRepository()
    dependencies = build_test_dependencies(repository)
    mcp = FakeMcp()
    register_query_tools(mcp, dependencies)

    result = mcp.tools["find_documents"](
        database="osco",
        collection="order",
        filter={"_id": "507f1f77bcf86cd799439011"},
        limit=5,
        sanitize=False,
    )

    assert result["count_returned"] == 1
    assert result["documents"] == [
        {
            "_id": "507f1f77bcf86cd799439011",
            "extra": "not projected by fake repo",
            "customer": {"email": [{"_id": "person@example.com", "type": "PERSONAL"}]},
        }
    ]
    assert result["sanitized"] is False
    assert result["elapsed_ms"] >= 0
    assert repository.last_projection is None


def test_find_documents_tool_sanitizes_by_default() -> None:
    repository = FakeRepository()
    dependencies = build_test_dependencies(repository)
    mcp = FakeMcp()
    register_query_tools(mcp, dependencies)

    result = mcp.tools["find_documents"](
        database="osco",
        collection="order",
        filter={"_id": "507f1f77bcf86cd799439011"},
        limit=5,
    )

    assert result["count_returned"] == 1
    assert result["documents"] == [
        {
            "_id": "507f1f77bcf86cd799439011",
            "extra": "not projected by fake repo",
            "customer": {"email": [{"type": "PERSONAL"}]},
        }
    ]
    assert result["sanitized"] is True
    assert repository.last_projection["_id"] == 1


class EmptyRepository(FakeRepository):
    def count(self, database, collection, filter):
        return 0

    def aggregate(self, database, collection, pipeline, limit):
        return []


def _dependencies_for_host(repository: FakeRepository, host: str) -> AppDependencies:
    dependencies = build_test_dependencies(repository)
    settings = LocalTestSettings(
        MCP_SCHEMA_RESOURCE_DIR=Path("backend/app/resources"), MONGODB_URI=f"mongodb+srv://reader:secret@{host}/"
    )
    return AppDependencies(**{**dependencies.__dict__, "settings": settings})


def _prod_dependencies(repository: FakeRepository) -> AppDependencies:
    return _dependencies_for_host(repository, "osco-prod-mongodb-01-pl-0.example.mongodb.net")


def test_empty_order_history_in_prod_is_not_reported_as_missing_data() -> None:
    mcp = FakeMcp()
    register_query_tools(mcp, _prod_dependencies(EmptyRepository()))

    count = mcp.tools["count_documents"](database="osco", collection="orderHistory", filter={})
    aggregate = mcp.tools["aggregate_documents"](database="osco", collection="orderHistory", pipeline=[{"$match": {}}])

    assert count["environment"] == "prod"
    assert count["warnings"] == [PROD_EMPTY_ARCHIVE_WARNING]
    assert aggregate["warnings"] == [PROD_EMPTY_ARCHIVE_WARNING]


def _nonprod_dependencies(repository: FakeRepository) -> AppDependencies:
    return _dependencies_for_host(repository, "osco-qa2-mongodb-01.example.mongodb.net")


def test_no_prod_warning_for_other_collections() -> None:
    prod = FakeMcp()
    register_query_tools(prod, _prod_dependencies(EmptyRepository()))
    assert prod.tools["count_documents"](database="osco", collection="order", filter={})["warnings"] == []


def test_order_history_returns_zero_in_nonprod_without_querying() -> None:
    repository = RecordingRepository()
    mcp = FakeMcp()
    register_query_tools(mcp, _nonprod_dependencies(repository))

    count = mcp.tools["count_documents"](database="osco", collection="orderHistory", filter={})
    aggregate = mcp.tools["aggregate_documents"](database="osco", collection="orderHistory", pipeline=[{"$count": "n"}])
    find = mcp.tools["find_documents"](database="osco", collection="orderHistory", filter={})

    assert count["count"] == 0 and count["skipped"] is True and count["environment"] == "nonprod"
    assert aggregate["documents"] == [] and find["documents"] == []
    assert repository.last_filter is None and repository.last_pipeline is None
    # osco.order is still queried normally in nonprod.
    assert mcp.tools["count_documents"](database="osco", collection="order", filter={})["count"] == 1


def test_date_filters_match_dates_and_iso_strings() -> None:
    repository = RecordingRepository()
    mcp = FakeMcp()
    register_query_tools(mcp, build_test_dependencies(repository))

    mcp.tools["count_documents"](
        database="osco",
        collection="orderHistory",
        filter={"orderCreatedDate": {"$gte": {"$date": "2025-10-28T05:00:00Z"}, "$lt": {"$date": "2025-10-29T05:00:00Z"}}},
    )

    start = datetime(2025, 10, 28, 5, tzinfo=timezone.utc)
    end = datetime(2025, 10, 29, 5, tzinfo=timezone.utc)
    assert repository.last_filter == {
        "$and": [
            {"$and": [{"$or": [
                {"orderCreatedDate": {"$gte": start, "$lt": end}},
                {"orderCreatedDate": {"$gte": "2025-10-28T05:00:00", "$lt": "2025-10-29T05:00:00"}},
            ]}]},
            {"isActive": True},
        ]
    }


class RecordingRepository(FakeRepository):
    def __init__(self) -> None:
        super().__init__()
        self.last_filter = None
        self.last_pipeline = None

    def count(self, database, collection, filter):
        self.last_filter = filter
        return 1

    def aggregate(self, database, collection, pipeline, limit):
        self.last_pipeline = pipeline
        return [{"orders": 1}]


def test_order_counts_only_active_version_by_default() -> None:
    repository = RecordingRepository()
    mcp = FakeMcp()
    register_query_tools(mcp, build_test_dependencies(repository))

    count = mcp.tools["count_documents"](database="osco", collection="orderHistory", filter={"banner": "safeway"})
    assert count["active_only_applied"] is True
    assert repository.last_filter == {"$and": [{"banner": "safeway"}, {"isActive": True}]}

    mcp.tools["count_documents"](database="osco", collection="order", filter={})
    assert repository.last_filter == {"isActive": True}

    aggregate = mcp.tools["aggregate_documents"](
        database="osco", collection="orderHistory", pipeline=[{"$group": {"_id": "$orderNumber"}}, {"$count": "orders"}]
    )
    assert aggregate["active_only_applied"] is True
    assert repository.last_pipeline[0] == {"$match": {"isActive": True}}


def test_active_only_can_be_disabled_or_overridden() -> None:
    repository = RecordingRepository()
    mcp = FakeMcp()
    register_query_tools(mcp, build_test_dependencies(repository))

    every_version = mcp.tools["count_documents"](database="osco", collection="orderHistory", filter={}, active_only=False)
    assert every_version["active_only_applied"] is False
    assert repository.last_filter == {}

    # An explicit isActive condition is left as the caller wrote it.
    mcp.tools["count_documents"](database="osco", collection="order", filter={"isActive": False})
    assert repository.last_filter == {"isActive": False}

    pipeline = [{"$match": {"banner": "safeway"}}]
    other = mcp.tools["aggregate_documents"](database="osco", collection="store", pipeline=pipeline)
    assert other["active_only_applied"] is False
    assert repository.last_pipeline == pipeline


class WindowRepository(FakeRepository):
    """Counts one order per hour in the requested window; fails windows longer than `fail_over_days`."""

    def __init__(self, fail_over_days: int | None = None, history_ends: datetime | None = None) -> None:
        super().__init__()
        self.calls: list[tuple[str, datetime, datetime]] = []
        self.fail_over_days = fail_over_days
        # Newest orderCreatedDate in osco.orderHistory, i.e. where count_orders hands off to osco.order.
        self.history_ends = history_ends or datetime(2026, 7, 30, 11, 59, 59, 999000, tzinfo=timezone.utc)

    def find(self, database, collection, filter, projection, sort, limit):
        assert (collection, sort) == ("orderHistory", [("orderCreatedDate", -1)])
        return [{"orderCreatedDate": self.history_ends.replace(tzinfo=None)}]  # json_util returns naive UTC

    def count(self, database, collection, filter):
        window = _find_date_window(filter)
        self.calls.append((collection, *window))
        days = (window[1] - window[0]).total_seconds() / 86400
        if self.fail_over_days is not None and days > self.fail_over_days:
            raise DataApiError("Data API POST /count failed: ReadTimeout")
        return round(days * 24)


def _find_date_window(value):
    if isinstance(value, dict):
        condition = value.get("orderCreatedDate")
        if isinstance(condition, dict) and isinstance(condition.get("$gte"), datetime):
            return condition["$gte"], condition["$lt"]
        for nested in value.values():
            found = _find_date_window(nested)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_date_window(item)
            if found:
                return found
    return None


def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(query_tools, "_now", lambda: datetime(2026, 10, 30, 12, tzinfo=timezone.utc))


def test_count_orders_splits_last_six_months_between_collections(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_now(monkeypatch)
    repository = WindowRepository()
    mcp = FakeMcp()
    register_query_tools(mcp, _prod_dependencies(repository))

    result = mcp.tools["count_orders"](start="2026-04-30T12:00:00Z", end="2026-10-30T12:00:00Z")

    assert result["order_cutoff"] == "2026-07-30T12:00:00Z"
    assert result["cutoff_source"] == "newest orderCreatedDate in osco.orderHistory"
    assert [(p["collection"], p["from"], p["to"]) for p in result["parts"]] == [
        ("osco.order", "2026-07-30T12:00:00Z", "2026-10-30T12:00:00Z"),
        ("osco.orderHistory", "2026-04-30T12:00:00Z", "2026-07-30T12:00:00Z"),
    ]
    assert result["total"] == (92 + 91) * 24  # one fake order per hour, no overlap at the cutoff
    assert {collection for collection, *_ in repository.calls} == {"order", "orderHistory"}


def test_count_orders_uses_only_order_history_for_last_year(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_now(monkeypatch)
    repository = WindowRepository()
    mcp = FakeMcp()
    register_query_tools(mcp, _prod_dependencies(repository))

    result = mcp.tools["count_orders"](start="2025-10-28T05:00:00Z", end="2025-10-29T05:00:00Z")

    assert [p["collection"] for p in result["parts"]] == ["osco.orderHistory"]
    assert result["total"] == 24 and result["warnings"] == []


def test_count_orders_in_nonprod_queries_only_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_now(monkeypatch)
    repository = WindowRepository()
    mcp = FakeMcp()
    register_query_tools(mcp, _nonprod_dependencies(repository))

    result = mcp.tools["count_orders"](start="2025-10-28T05:00:00Z", end="2025-10-29T05:00:00Z")

    assert [p["collection"] for p in result["parts"]] == ["osco.order"]
    assert {collection for collection, *_ in repository.calls} == {"order"}
    assert NONPROD_SKIPPED_NOTE in result["warnings"]


def test_count_orders_splits_windows_that_time_out(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_now(monkeypatch)
    repository = WindowRepository(fail_over_days=10)
    mcp = FakeMcp()
    register_query_tools(mcp, _prod_dependencies(repository))

    result = mcp.tools["count_orders"](start="2025-01-01T00:00:00Z", end="2025-02-01T00:00:00Z")

    assert result["total"] == 31 * 24
    assert result["parts"][0]["queries"] > 1


def test_count_orders_hands_off_where_order_history_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real prod layout on 2026-10-30: orderHistory ends 2026-05-03, so "last 6 months" is mostly osco.order.
    _freeze_now(monkeypatch)
    repository = WindowRepository(history_ends=datetime(2026, 5, 3, 23, 59, 59, 782000, tzinfo=timezone.utc))
    mcp = FakeMcp()
    register_query_tools(mcp, _prod_dependencies(repository))

    result = mcp.tools["count_orders"](start="2026-04-30T05:00:00Z", end="2026-10-30T05:00:00Z")

    assert result["order_cutoff"] == "2026-05-03T23:59:59.783Z"
    assert [(p["collection"], p["from"], p["to"]) for p in result["parts"]] == [
        ("osco.order", "2026-05-03T23:59:59.783Z", "2026-10-30T05:00:00Z"),
        ("osco.orderHistory", "2026-04-30T05:00:00Z", "2026-05-03T23:59:59.783Z"),
    ]


def test_count_orders_falls_back_to_three_months_if_handoff_lookup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_now(monkeypatch)
    repository = WindowRepository()

    def failing_find(*args, **kwargs):
        raise DataApiError("Data API POST /find failed: ReadTimeout")

    repository.find = failing_find
    mcp = FakeMcp()
    register_query_tools(mcp, _prod_dependencies(repository))

    result = mcp.tools["count_orders"](start="2026-04-30T12:00:00Z", end="2026-10-30T12:00:00Z")

    assert result["order_cutoff"] == "2026-07-30T12:00:00Z"
    assert result["cutoff_source"].startswith("fallback")
    assert any("Could not read" in warning for warning in result["warnings"])


def test_count_orders_always_uses_order_history_for_dates_a_year_old(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even if orderHistory appeared to end earlier (e.g. archiving lagged), last year's dates still come from it.
    _freeze_now(monkeypatch)
    repository = WindowRepository(history_ends=datetime(2025, 6, 1, tzinfo=timezone.utc))
    mcp = FakeMcp()
    register_query_tools(mcp, _prod_dependencies(repository))

    result = mcp.tools["count_orders"](start="2025-10-28T05:00:00Z", end="2025-10-29T05:00:00Z")

    assert [p["collection"] for p in result["parts"]] == ["osco.orderHistory"]
    assert result["order_cutoff"] == "2025-10-30T12:00:00Z"
    assert result["cutoff_source"] == "12 months before now (last-year rule)"
