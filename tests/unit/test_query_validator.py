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


@pytest.mark.parametrize(
    "pipeline",
    [
        [{"$group": {"_id": "$customer.email.id", "n": {"$sum": 1}}}],
        [{"$addFields": {"who": "$customer.name.firstName"}}],
        [{"$addFields": {"who": {"$concat": ["x", "$customer.name.lastName"]}}}],
        [{"$group": {"_id": None, "docs": {"$push": "$$ROOT"}}}],
        [{"$addFields": {"c": "$$CURRENT.customer.contact.number"}}],
        [{"$addFields": {"c": {"$getField": "customer"}}}],
        [{"$group": {"_id": "$customer"}}],
        [{"$project": {"customer.email.id": 1}}],
    ],
)
def test_pipeline_cannot_read_pii_under_another_name(pipeline) -> None:
    query_validator, schema = validator()

    with pytest.raises(ValidationError):
        query_validator.validate_pipeline(schema, pipeline)


def test_pipeline_cannot_read_fields_missing_from_the_catalog() -> None:
    query_validator, schema = validator()

    with pytest.raises(ValidationError, match="not a field"):
        query_validator.validate_pipeline(schema, [{"$group": {"_id": "$phoneAlt"}}])


def test_pipeline_can_read_safe_fields_and_computed_fields() -> None:
    query_validator, schema = validator()

    query_validator.validate_pipeline(schema, [
        {"$match": {"customer.customerId": "123"}},  # filtering on PII is fine; returning it is not
        {"$addFields": {"day": {"$dateToString": {"format": "%Y-%m-%d", "date": {"$toDate": "$orderCreatedDate"}}}}},
        {"$group": {"_id": {"day": "$day", "store": "$storeNumber"}, "n": {"$sum": 1},
                    "revenue": {"$sum": {"$convert": {"input": "$orderTotal.amount", "to": "decimal", "onError": 0}}}}},
    ])


def test_pii_reads_are_allowed_when_the_schema_turns_sanitize_off() -> None:
    query_validator, schema = validator()
    raw = schema.model_copy(update={"sanitize": False})

    query_validator.validate_pipeline(raw, [{"$group": {"_id": "$customer.email.id"}}])
