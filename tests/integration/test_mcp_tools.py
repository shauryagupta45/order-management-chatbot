from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.catalog.yaml_schema_catalog import YamlSchemaCatalog
from backend.app.config import LocalTestSettings
from backend.app.dependencies import AppDependencies
from backend.app.services.projection_policy import ProjectionPolicy
from backend.app.services.query_builder import QueryBuilder
from backend.app.services.query_validator import QueryValidator
from backend.app.services.response_sanitizer import ResponseSanitizer
from backend.app.services.target_resolver import TargetResolver
from backend.app.tools.query_tools import PROD_EMPTY_ARCHIVE_WARNING, register_query_tools


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


def test_no_prod_warning_outside_prod_or_for_other_collections() -> None:
    nonprod = FakeMcp()
    register_query_tools(nonprod, _dependencies_for_host(EmptyRepository(), "osco-qa2-mongodb-01.example.mongodb.net"))
    prod = FakeMcp()
    register_query_tools(prod, _prod_dependencies(EmptyRepository()))

    nonprod_count = nonprod.tools["count_documents"](database="osco", collection="orderHistory", filter={})
    assert nonprod_count["environment"] == "nonprod"
    assert nonprod_count["warnings"] == []
    assert prod.tools["count_documents"](database="osco", collection="order", filter={})["warnings"] == []


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
