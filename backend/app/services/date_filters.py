"""Make date filters match fields that hold a BSON date in some documents and an ISO-8601 string in others.

MongoDB never compares a date with a string, so {"orderCreatedDate": {"$gte": <date>}} silently skips documents where
the value is "2025-07-03T06:39:58.331Z". For fields marked `mixed_date_string: true` in the schema, each date
condition is rewritten into an $or of the same condition twice: once with BSON dates, once with ISO strings.

String bounds are written without a timezone suffix ("2025-10-28T05:00:00"). Values are UTC whether they end in "Z"
or not ("...58.331Z", "...58.331000"), and a prefix-style bound sorts correctly against both: "2025-10-28T05:00:00.5Z"
is >= "2025-10-28T05:00:00" and < "2025-10-29T05:00:00".
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.app.domain.schema_models import CollectionSchema

# Only these operators are rewritten; any other operator on the field ($ne, $in, $regex) leaves the condition as-is.
RANGE_OPERATORS = {"$eq", "$gt", "$gte", "$lt", "$lte"}
LOGICAL_OPERATORS = {"$and", "$or"}


def parse_instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, dict) and set(value) == {"$date"}:
        return parse_instant(value["$date"])
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_string(instant: datetime) -> str:
    text = instant.strftime("%Y-%m-%dT%H:%M:%S")
    if instant.microsecond:
        text += f".{instant.microsecond:06d}".rstrip("0")
    return text


def _expand_condition(field: str, condition: Any) -> dict[str, Any] | None:
    operators = condition if isinstance(condition, dict) and set(condition) != {"$date"} else {"$eq": condition}
    if not operators or not set(operators) <= RANGE_OPERATORS:
        return None
    instants = {operator: parse_instant(value) for operator, value in operators.items()}
    if any(instant is None for instant in instants.values()):
        return None  # not a date comparison (e.g. {"$eq": null}); leave it untouched
    return {
        "$or": [
            # A real datetime: bson's json_util sends it as {"$date": ...} to the data API and pymongo uses it directly.
            {field: dict(instants)},
            {field: {operator: _as_string(instant) for operator, instant in instants.items()}},
        ]
    }


def expand_mixed_date_filter(schema: CollectionSchema, filter: dict[str, Any]) -> dict[str, Any]:
    """Return `filter` with every condition on a mixed date/string field matching both storage types."""
    result: dict[str, Any] = {}
    expansions: list[dict[str, Any]] = []
    for key, value in filter.items():
        if key in LOGICAL_OPERATORS and isinstance(value, list):
            result[key] = [expand_mixed_date_filter(schema, item) if isinstance(item, dict) else item for item in value]
            continue
        field = schema.get_field(key)
        expanded = _expand_condition(key, value) if field is not None and field.mixed_date_string else None
        if expanded is None:
            result[key] = value
        else:
            expansions.append(expanded)
    if expansions:
        # Several fields can each need an $or, so they are combined under $and alongside any existing $and.
        result["$and"] = [*result.get("$and", []), *expansions]
    return result


def expand_mixed_date_pipeline(schema: CollectionSchema, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"$match": expand_mixed_date_filter(schema, stage["$match"])}
        if isinstance(stage, dict) and isinstance(stage.get("$match"), dict)
        else stage
        for stage in pipeline
    ]
