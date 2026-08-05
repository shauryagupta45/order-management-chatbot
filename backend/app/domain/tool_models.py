from __future__ import annotations

from pydantic import BaseModel, Field


class ResolvedTarget(BaseModel):
    database: str | None = None
    collection: str | None = None
    candidate_fields: list[str] = Field(default_factory=list)
    confidence: str = "low"
    explanation: str = ""
    warnings: list[str] = Field(default_factory=list)

