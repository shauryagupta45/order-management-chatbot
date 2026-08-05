from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

try:
    from bson import Decimal128, ObjectId
except ImportError:  # pragma: no cover
    Decimal128 = None
    ObjectId = None

from backend.app.domain.schema_models import CollectionSchema

OBJECT_ID_STRING_RE = re.compile(r'^ObjectId\("([0-9a-fA-F]{24})"\)$')


class ResponseSanitizer:
    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes

    def sanitize_documents(self, schema: CollectionSchema, documents: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        return self.format_documents(schema, documents, sanitize=True)

    def serialize_documents(self, documents: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        return self.format_documents(None, documents, sanitize=False)

    def format_documents(
        self, schema: CollectionSchema | None, documents: list[dict[str, Any]], sanitize: bool
    ) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        formatted = [
            self._sanitize_value(self._remove_sensitive_fields(schema, document) if sanitize and schema else document)
            for document in documents
        ]
        encoded = json.dumps(formatted, default=str)
        if len(encoded.encode("utf-8")) > self._max_bytes:
            warnings.append("Response exceeded MCP_MAX_BYTES_PER_RESPONSE and was truncated.")
            while formatted and len(json.dumps(formatted, default=str).encode("utf-8")) > self._max_bytes:
                formatted.pop()
        return formatted, warnings

    def _remove_sensitive_fields(self, schema: CollectionSchema, document: dict[str, Any]) -> dict[str, Any]:
        clean = dict(document)
        for field_name, field_schema in schema.fields.items():
            if field_schema.pii or field_schema.sensitive:
                self._delete_path(clean, field_name.split("."))
        return clean

    def _delete_path(self, document: dict[str, Any], path: list[str]) -> None:
        if not path:
            return
        if isinstance(document, list):
            for item in document:
                if isinstance(item, dict):
                    self._delete_path(item, path)
            return
        if not isinstance(document, dict):
            return
        if len(path) == 1:
            document.pop(path[0], None)
            return
        next_value = document.get(path[0])
        if isinstance(next_value, list):
            for item in next_value:
                if isinstance(item, dict):
                    self._delete_path(item, path[1:])
        elif isinstance(next_value, dict):
            self._delete_path(next_value, path[1:])

    def _sanitize_value(self, value: Any) -> Any:
        if ObjectId is not None and isinstance(value, ObjectId):
            return str(value)
        if Decimal128 is not None and isinstance(value, Decimal128):
            return str(value.to_decimal())
        if isinstance(value, str):
            match = OBJECT_ID_STRING_RE.match(value)
            if match:
                return match.group(1)
            return value
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {key: self._sanitize_value(nested) for key, nested in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        return value
