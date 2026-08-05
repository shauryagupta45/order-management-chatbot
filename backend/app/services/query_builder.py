from __future__ import annotations

from typing import Any


class QueryBuilder:
    def build_exact_match_filter(self, field_values: dict[str, Any]) -> dict[str, Any]:
        return {field: value for field, value in field_values.items() if value is not None}

