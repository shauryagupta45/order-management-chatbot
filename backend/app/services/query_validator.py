from __future__ import annotations

from typing import Any

from bson import ObjectId

from backend.app.catalog.base import SchemaCatalog
from backend.app.domain.errors import ValidationError
from backend.app.domain.schema_models import CollectionSchema


BLOCKED_KEYS = {"$where", "$function", "$accumulator", "$out", "$merge"}
SAFE_OPERATORS = {"$eq", "$in", "$gt", "$gte", "$lt", "$lte", "$ne", "$regex"}
LOGICAL_OPERATORS = {"$and", "$or"}
SAFE_STAGES = {"$match", "$project", "$sort", "$limit", "$skip", "$group", "$unwind", "$addFields", "$count"}


class QueryValidator:
    def __init__(self, catalog: SchemaCatalog, max_documents_per_query: int, allow_aggregate: bool = True) -> None:
        self._catalog = catalog
        self._max_documents_per_query = max_documents_per_query
        self._allow_aggregate = allow_aggregate

    def get_schema(self, database: str, collection: str) -> CollectionSchema:
        if not database or not collection:
            raise ValidationError("Database and collection are required.")
        return self._catalog.get_collection_schema(database, collection)

    def cap_limit(self, limit: int | None, default_limit: int = 10) -> int:
        requested = default_limit if limit is None else limit
        if requested < 1:
            raise ValidationError("Limit must be at least 1.")
        return min(requested, self._max_documents_per_query)

    def validate_filter(self, schema: CollectionSchema, filter: dict[str, Any]) -> None:
        self._reject_blocked_recursive(filter)
        for key, value in filter.items():
            if key in LOGICAL_OPERATORS:
                if not isinstance(value, list):
                    raise ValidationError(f"Logical operator '{key}' requires a list of filters.")
                for item in value:
                    if not isinstance(item, dict):
                        raise ValidationError(f"Logical operator '{key}' contains a non-object filter.")
                    self.validate_filter(schema, item)
                continue
            if key.startswith("$"):
                raise ValidationError(f"Operator '{key}' is not allowed at the top level.")
            field_schema = schema.get_field(key)
            if field_schema is None:
                raise ValidationError(f"Unknown filter field '{key}' in {schema.database}.{schema.collection}.")
            if not field_schema.filterable and not field_schema.searchable:
                raise ValidationError(f"Field '{key}' is not marked filterable/searchable.")
            if isinstance(value, dict):
                for operator in value:
                    if operator not in SAFE_OPERATORS:
                        raise ValidationError(f"Operator '{operator}' is not allowed.")
                    if operator == "$regex" and not field_schema.allow_regex:
                        raise ValidationError(f"Regex is not allowed for field '{key}'.")

    def normalize_filter(self, schema: CollectionSchema, filter: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in filter.items():
            if key in LOGICAL_OPERATORS:
                normalized[key] = [self.normalize_filter(schema, item) for item in value]
                continue
            field_schema = schema.get_field(key)
            if field_schema is None:
                normalized[key] = value
                continue
            if isinstance(value, dict):
                normalized[key] = {
                    operator: self._normalize_field_value(field_schema.type, operator_value)
                    for operator, operator_value in value.items()
                }
            else:
                normalized[key] = self._normalize_field_value(field_schema.type, value)
        return normalized

    def validate_sort(self, schema: CollectionSchema, sort: dict[str, int | str] | None) -> list[tuple[str, int]] | None:
        if not sort:
            if schema.default_sort:
                direction = -1 if schema.default_sort.direction == "desc" else 1
                return [(schema.default_sort.field, direction)]
            return None
        normalized: list[tuple[str, int]] = []
        for field_name, direction in sort.items():
            field_schema = schema.get_field(field_name)
            if field_schema is None:
                raise ValidationError(f"Unknown sort field '{field_name}' in {schema.database}.{schema.collection}.")
            if not field_schema.sortable:
                raise ValidationError(f"Field '{field_name}' is not sortable.")
            normalized.append((field_name, self._normalize_direction(direction)))
        return normalized

    def validate_pipeline(self, schema: CollectionSchema, pipeline: list[dict[str, Any]]) -> None:
        if not self._allow_aggregate:
            raise ValidationError("Aggregation tools are disabled by configuration.")
        if not isinstance(pipeline, list):
            raise ValidationError("Aggregation pipeline must be a list.")
        computed_fields: set[str] = set()
        for stage in pipeline:
            if not isinstance(stage, dict) or len(stage) != 1:
                raise ValidationError("Each aggregation stage must be an object with exactly one stage operator.")
            stage_name, stage_value = next(iter(stage.items()))
            if stage_name in BLOCKED_KEYS or stage_name not in SAFE_STAGES:
                raise ValidationError(f"Aggregation stage '{stage_name}' is not allowed.")
            self._reject_blocked_recursive(stage_value)
            if stage_name == "$addFields":
                if not isinstance(stage_value, dict):
                    raise ValidationError("$addFields stage must be an object.")
                computed_fields.update(stage_value.keys())
            elif stage_name == "$match":
                if not isinstance(stage_value, dict):
                    raise ValidationError("$match stage must be an object.")
                # Allow matching on computed fields introduced by preceding $addFields stages
                schema_only_filter = {k: v for k, v in stage_value.items() if k not in computed_fields}
                if schema_only_filter:
                    self.validate_filter(schema, schema_only_filter)
            elif stage_name == "$project":
                if not isinstance(stage_value, dict):
                    raise ValidationError("$project stage must be an object.")
                for field_name, enabled in stage_value.items():
                    if field_name == "_id" and enabled in (0, 1, False, True):
                        continue
                    self._validate_field_reference(schema, field_name, "project")
            elif stage_name == "$sort":
                if not isinstance(stage_value, dict):
                    raise ValidationError("$sort stage must be an object.")
                self.validate_sort(schema, stage_value)

    def _reject_blocked_recursive(self, value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in BLOCKED_KEYS:
                    raise ValidationError(f"Dangerous MongoDB operator/stage '{key}' is not allowed.")
                self._reject_blocked_recursive(nested)
        elif isinstance(value, list):
            for item in value:
                self._reject_blocked_recursive(item)

    def _validate_field_reference(self, schema: CollectionSchema, field_name: str, context: str) -> None:
        clean_name = field_name[1:] if field_name.startswith("$") else field_name
        if clean_name and clean_name not in schema.fields:
            raise ValidationError(f"Unknown {context} field '{clean_name}' in {schema.database}.{schema.collection}.")

    def _normalize_direction(self, direction: int | str) -> int:
        if direction in (1, "1", "asc", "ASC"):
            return 1
        if direction in (-1, "-1", "desc", "DESC"):
            return -1
        raise ValidationError("Sort direction must be 1, -1, asc, or desc.")

    def _normalize_field_value(self, field_type: str, value: Any) -> Any:
        if field_type != "objectId":
            return value
        if isinstance(value, list):
            return [self._normalize_field_value(field_type, item) for item in value]
        if isinstance(value, ObjectId):
            return value
        if isinstance(value, str) and ObjectId.is_valid(value):
            return ObjectId(value)
        raise ValidationError("ObjectId fields require a valid 24-character ObjectId string.")
