from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.catalog.yaml_schema_catalog import YamlSchemaCatalog
from backend.app.domain.errors import ValidationError
from backend.app.services.projection_policy import ProjectionPolicy
from backend.app.services.response_sanitizer import ResponseSanitizer


def schema():
    return YamlSchemaCatalog(Path("backend/app/resources")).get_collection_schema("osco", "order")


def test_default_projection_returns_known_safe_fields() -> None:
    projection = ProjectionPolicy().build_projection(schema(), None)

    assert projection["_id"] == 1
    assert projection["orderNumber"] == 1
    assert projection["versionNumber"] == 1
    assert "customer.customerId" not in projection
    assert "customer.email.id" not in projection
    assert "paymentDetails.tokenNumber" not in projection


def test_unknown_projection_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Unknown projection field"):
        ProjectionPolicy().build_projection(schema(), {"unknown": 1})


def test_requested_inclusion_projection_does_not_mix_sensitive_exclusions() -> None:
    projection = ProjectionPolicy().build_projection(schema(), {"orderNumber": 1, "orderStatus": 1})

    assert projection == {"orderNumber": 1, "orderStatus": 1}


def test_requested_exclusion_projection_adds_sensitive_exclusions() -> None:
    projection = ProjectionPolicy().build_projection(schema(), {"pickerFirstName": 0})

    assert projection["pickerFirstName"] == 0
    assert projection["customer.email._id"] == 0
    assert projection["tenderAllocationSummary.tokenNumber"] == 0


def test_response_sanitizer_returns_known_safe_document() -> None:
    documents, warnings = ResponseSanitizer(max_bytes=100_000).sanitize_documents(
        schema(),
        [{"_id": "507f1f77bcf86cd799439011"}],
    )

    assert warnings == []
    assert documents == [{"_id": "507f1f77bcf86cd799439011"}]


def test_response_sanitizer_normalizes_objectid_string_repr() -> None:
    documents, _ = ResponseSanitizer(max_bytes=100_000).sanitize_documents(
        schema(),
        [{"_id": 'ObjectId("6334c7639df354002036a36d")'}],
    )

    assert documents == [{"_id": "6334c7639df354002036a36d"}]


def test_response_sanitizer_removes_sensitive_fields_inside_arrays() -> None:
    documents, _ = ResponseSanitizer(max_bytes=100_000).sanitize_documents(
        schema(),
        [
            {
                "customer": {"email": [{"_id": "person@example.com", "type": "PERSONAL"}]},
                "tenderAllocationSummary": [{"tokenNumber": "tok_123", "amount": "10.28"}],
                "subOrders": [
                    {
                        "charges": [{"tenderAllocations": [{"tokenNumber": "tok_nested", "amount": "1.00"}]}],
                        "customerService": {"contact": [{"number": "5551112222", "subType": "STORE_PHONE"}]},
                    }
                ],
            }
        ],
    )

    assert documents == [
        {
            "customer": {"email": [{"type": "PERSONAL"}]},
            "tenderAllocationSummary": [{"amount": "10.28"}],
            "subOrders": [
                {
                    "charges": [{"tenderAllocations": [{"amount": "1.00"}]}],
                    "customerService": {"contact": [{"subType": "STORE_PHONE"}]},
                }
            ],
        }
    ]


def test_response_serializer_keeps_sensitive_fields_when_not_sanitizing() -> None:
    documents, _ = ResponseSanitizer(max_bytes=100_000).serialize_documents(
        [{"customer": {"email": [{"_id": "person@example.com"}]}}]
    )

    assert documents == [{"customer": {"email": [{"_id": "person@example.com"}]}}]


def test_pipeline_returning_whole_documents_gets_the_safe_projection() -> None:
    pipeline = [{"$match": {"orderNumber": "1"}}, {"$addFields": {"day": "x"}}]

    secured = ProjectionPolicy().secure_pipeline(schema(), pipeline)

    assert secured[:2] == pipeline
    projection = secured[-1]["$project"]
    assert projection["orderNumber"] == 1 and projection["day"] == 1
    assert "customer.email.id" not in projection and "customer.customerId" not in projection


@pytest.mark.parametrize(
    "pipeline",
    [[{"$group": {"_id": "$storeNumber"}}], [{"$count": "n"}], [{"$project": {"orderNumber": 1}}]],
)
def test_pipeline_that_reshapes_output_is_left_alone(pipeline) -> None:
    assert ProjectionPolicy().secure_pipeline(schema(), pipeline) == pipeline


def test_exclusion_project_still_gets_the_safe_projection() -> None:
    secured = ProjectionPolicy().secure_pipeline(schema(), [{"$project": {"orderNumber": 0}}])

    assert len(secured) == 2
