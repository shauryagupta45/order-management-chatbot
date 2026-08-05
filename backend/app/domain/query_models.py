from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FindQuery(BaseModel):
    database: str
    collection: str
    filter: dict[str, Any] = Field(default_factory=dict)
    projection: dict[str, int | bool] | None = None
    sort: dict[str, int | str] | None = None
    limit: int = 10


class AggregateQuery(BaseModel):
    database: str
    collection: str
    pipeline: list[dict[str, Any]]
    limit: int = 10

