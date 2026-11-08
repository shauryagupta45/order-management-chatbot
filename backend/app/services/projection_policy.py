from __future__ import annotations

from typing import Any

from backend.app.domain.errors import ValidationError
from backend.app.domain.schema_models import CollectionSchema


class ProjectionPolicy:
    def __init__(self, allow_pii_projection: bool = False) -> None:
        self._allow_pii_projection = allow_pii_projection

    def build_projection(
        self, schema: CollectionSchema, requested_projection: dict[str, int | bool] | None
    ) -> dict[str, int]:
        if requested_projection is None:
            projection = {
                field_name: 1
                for field_name, field_schema in schema.fields.items()
                if field_schema.return_by_default and field_schema.returnable
            }
            return projection or {"_id": 1}

        projection: dict[str, int] = {}
        requested_values = [int(bool(enabled)) for enabled in requested_projection.values()]
        is_inclusion_projection = any(value == 1 for value in requested_values)
        for field_name, enabled in requested_projection.items():
            field_schema = schema.get_field(field_name)
            if field_schema is None:
                raise ValidationError(f"Unknown projection field '{field_name}' in {schema.database}.{schema.collection}.")
            include = int(bool(enabled))
            if include and not field_schema.returnable and not self._allow_pii_projection:
                raise ValidationError(f"Field '{field_name}' is marked PII/sensitive and cannot be returned.")
            projection[field_name] = include

        if not is_inclusion_projection:
            for field_name, field_schema in schema.fields.items():
                if not field_schema.returnable:
                    projection[field_name] = 0
        return projection

    def secure_pipeline(self, schema: CollectionSchema, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """A pipeline without $group, $count or an inclusion $project returns whole documents, including fields the
        catalog does not describe; end it with the projection find uses by default (plus fields it computed)."""
        if not schema.sanitize or any(_reshapes_output(stage) for stage in pipeline):
            return pipeline
        computed = {name: 1 for stage in pipeline for name in stage.get("$addFields", {})}
        return [*pipeline, {"$project": {**self.build_projection(schema, None), **computed}}]


def _reshapes_output(stage: dict[str, Any]) -> bool:
    if "$group" in stage or "$count" in stage:
        return True
    project = stage.get("$project")
    return isinstance(project, dict) and any(name != "_id" and value not in (0, False) for name, value in project.items())
