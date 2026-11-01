"""Read-only data API in front of MongoDB.

Run:  uvicorn backend.data_api.main:create_app --factory --port 8080

The MCP server sends already-built queries here (see backend/app/db/api_repository.py for the contract). Every
request is validated again against the schema catalog, so a bad query is rejected even if the caller skipped its
own checks. Only read operations exist; there is no write endpoint.
"""
from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any

from bson import json_util
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import PyMongoError

from backend.app.dependencies import AppDependencies, build_dependencies
from backend.app.domain.errors import McpMongoError, ValidationError
from backend.app.domain.schema_models import CollectionSchema
from backend.data_api.auth import build_authenticator
from backend.data_api.settings import DataApiSettings

logger = logging.getLogger("data_api")


class _Target(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database: str
    collection: str


class FindRequest(_Target):
    filter: dict[str, Any] = Field(default_factory=dict)
    projection: dict[str, int] | None = None
    sort: list[tuple[str, int | str]] | None = None
    limit: int = Field(default=10, ge=1)


class CountRequest(_Target):
    filter: dict[str, Any] = Field(default_factory=dict)


class AggregateRequest(_Target):
    pipeline: list[dict[str, Any]]
    limit: int = Field(default=10, ge=1)


class ExplainRequest(_Target):
    filter: dict[str, Any] = Field(default_factory=dict)


def _extended(value: Any) -> Any:
    """Turn Extended JSON markers ({"$oid": ...}, {"$date": ...}) back into BSON types."""
    return json_util.loads(json.dumps(value))


def _short(value: Any, limit: int = 1500) -> str:
    text = json_util.dumps(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _redact(text: str) -> str:
    return re.sub(r"(mongodb(?:\+srv)?://)[^@/\s]+@", r"\1<redacted>@", text)


def _json(payload: dict[str, Any]) -> Response:
    """Serialize with Extended JSON so ObjectId / dates / Decimal128 survive the round trip."""
    return Response(content=json_util.dumps(payload), media_type="application/json")


def _checked_projection(schema: CollectionSchema, projection: dict[str, int] | None) -> dict[str, int] | None:
    for field_name in projection or {}:
        if schema.get_field(field_name) is None:
            raise ValidationError(f"Unknown projection field '{field_name}' in {schema.database}.{schema.collection}.")
    return projection or None


def create_app(settings: DataApiSettings | None = None, dependencies: AppDependencies | None = None) -> FastAPI:
    # This service always talks to MongoDB directly, whatever DATA_ACCESS_MODE says for the MCP server.
    resolved_settings = settings or DataApiSettings(DATA_ACCESS_MODE="mongodb")
    deps = dependencies or build_dependencies(resolved_settings)
    validator = deps.query_validator
    repository = deps.repository

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if deps.mongo_client_provider is not None:
            deps.mongo_client_provider.close()

    app = FastAPI(title="OSCO Data API", version="0.1.0", lifespan=lifespan)
    authenticated = [Depends(build_authenticator(resolved_settings))]

    @app.exception_handler(McpMongoError)
    async def handle_bad_query(request: Request, exc: McpMongoError) -> JSONResponse:
        logger.warning("%s rejected: %s", request.url.path, exc)
        return JSONResponse(status_code=400, content={"status": "BAD_REQUEST", "message": str(exc)})

    @app.exception_handler(PyMongoError)
    async def handle_database_error(request: Request, exc: PyMongoError) -> JSONResponse:
        # The client only sees a generic 502; the real cause (timeout, auth, network...) goes to this log.
        logger.error("%s database request failed: %s: %s", request.url.path, exc.__class__.__name__, _redact(str(exc))[:1500])
        return JSONResponse(
            status_code=502, content={"status": "BAD_GATEWAY", "message": "Database request failed"}
        )

    # Endpoints are plain `def` on purpose: pymongo is blocking, so FastAPI runs them in its thread pool.

    @app.get("/health", dependencies=authenticated)
    def health() -> dict[str, bool]:
        repository.ping()
        return {"ok": True}

    @app.post("/find", dependencies=authenticated)
    def find(request: FindRequest) -> Response:
        schema = validator.get_schema(request.database, request.collection)
        limit = validator.cap_limit(request.limit, schema.default_limit)
        filter_ = _extended(request.filter)
        validator.validate_filter(schema, filter_)
        documents = repository.find(
            database=request.database,
            collection=request.collection,
            filter=validator.normalize_filter(schema, filter_),
            projection=_checked_projection(schema, request.projection),
            sort=validator.validate_sort(schema, dict(request.sort) if request.sort else None),
            limit=limit,
        )
        logger.info("find %s.%s filter=%s limit=%d -> %d documents", request.database, request.collection,
                    _short(filter_), limit, len(documents))
        return _json({"documents": documents})

    @app.post("/count", dependencies=authenticated)
    def count(request: CountRequest) -> Response:
        schema = validator.get_schema(request.database, request.collection)
        filter_ = _extended(request.filter)
        validator.validate_filter(schema, filter_)
        total = repository.count(request.database, request.collection, validator.normalize_filter(schema, filter_))
        logger.info("count %s.%s filter=%s -> %d", request.database, request.collection, _short(filter_), total)
        return _json({"count": total})

    @app.post("/aggregate", dependencies=authenticated)
    def aggregate(request: AggregateRequest) -> Response:
        schema = validator.get_schema(request.database, request.collection)
        limit = validator.cap_limit(request.limit, schema.default_limit)
        pipeline = _extended(request.pipeline)
        validator.validate_pipeline(schema, pipeline)
        documents = repository.aggregate(request.database, request.collection, pipeline, limit)
        logger.info("aggregate %s.%s pipeline=%s -> %d documents", request.database, request.collection,
                    _short(pipeline), len(documents))
        return _json({"documents": documents})

    @app.post("/explain", dependencies=authenticated)
    def explain(request: ExplainRequest) -> Response:
        schema = validator.get_schema(request.database, request.collection)
        filter_ = _extended(request.filter)
        validator.validate_filter(schema, filter_)
        plan = repository.explain_find(request.database, request.collection, validator.normalize_filter(schema, filter_))
        return _json({"explain": plan})

    return app


__all__ = ["create_app"]

if __name__ == "__main__":
    import uvicorn

    from backend.app.logging_config import configure_logging

    configure_logging("INFO")  # without this, the per-query info lines are swallowed
    uvicorn.run(create_app(), host="127.0.0.1", port=8081)
