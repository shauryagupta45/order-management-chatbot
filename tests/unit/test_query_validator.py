from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.catalog.yaml_schema_catalog import YamlSchemaCatalog
from backend.app.domain.errors import ValidationError
from backend.app.services.query_validator import QueryValidator


def validator() -> tuple[QueryValidator, object]:
    catalog = YamlSchemaCatalog(Path("backend/app/resources"))
    query_validator = QueryValidator(catalog, max_documents_per_query=25)
    schema = catalog.get_collection_schema("osco", "order")
    return query_validator, schema


def test_rejects_where_operator() -> None:
    query_validator, schema = validator()

    with pytest.raises(ValidationError, match="Dangerous MongoDB operator"):
        query_validator.validate_filter(schema, {"$where": "this.status == 'PAID'"})


def test_rejects_function_operator() -> None:
    query_validator, schema = validator()

    with pytest.raises(ValidationError, match="Dangerous MongoDB operator"):
        query_validator.validate_filter(schema, {"_id": {"$function": {"body": "function() {}"}}})


def test_rejects_out_and_merge_stages() -> None:
    query_validator, schema = validator()

    with pytest.raises(ValidationError, match="not allowed"):
        query_validator.validate_pipeline(schema, [{"$out": "copy"}])
    with pytest.raises(ValidationError, match="not allowed"):
        query_validator.validate_pipeline(schema, [{"$merge": "copy"}])


def test_limit_capping() -> None:
    query_validator, _ = validator()

    assert query_validator.cap_limit(100) == 25


def test_unknown_field_rejection() -> None:
    query_validator, schema = validator()

    with pytest.raises(ValidationError, match="Unknown filter field"):
        query_validator.validate_filter(schema, {"notAField": "x"})


def test_rejects_unknown_starter_schema_field() -> None:
    query_validator, schema = validator()

    with pytest.raises(ValidationError, match="Unknown filter field"):
        query_validator.validate_filter(schema, {"createdAt": "2026-01-01"})
