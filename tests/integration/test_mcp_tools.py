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
from backend.app.tools.query_tools import register_query_tools


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


def test_find_documents_tool_sanitizes_when_requested() -> None:
    repository = FakeRepository()
    dependencies = build_test_dependencies(repository)
    mcp = FakeMcp()
    register_query_tools(mcp, dependencies)

    result = mcp.tools["find_documents"](
        database="osco",
        collection="order",
        filter={"_id": "507f1f77bcf86cd799439011"},
        limit=5,
        sanitize=True,
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
