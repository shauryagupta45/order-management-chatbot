from __future__ import annotations

import json

import httpx
import pytest
from bson import ObjectId, json_util

from backend.app.config import LocalTestSettings, Settings
from backend.app.db.api_repository import ApiRepository
from backend.app.domain.errors import DataApiError


def build_repository(handler, **settings) -> tuple[ApiRepository, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(
        base_url="https://data-api.example.invalid",
        transport=httpx.MockTransport(recording),
        headers={"Content-Type": "application/json"},
    )
    return ApiRepository(LocalTestSettings(**settings), client=client), seen


def json_response(payload, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=json_util.dumps(payload), headers={"Content-Type": "application/json"})


def test_find_posts_the_validated_query_and_returns_documents() -> None:
    oid = ObjectId()
    repo, seen = build_repository(lambda request: json_response({"documents": [{"_id": oid, "orderNumber": "1"}]}))

    documents = repo.find("osco", "order", {"_id": oid}, {"orderNumber": 1}, [("orderNumber", -1)], 5)

    assert documents == [{"_id": oid, "orderNumber": "1"}]
    request = seen[0]
    assert (request.method, request.url.path) == ("POST", "/find")
    body = json_util.loads(request.content.decode())
    assert body == {
        "database": "osco",
        "collection": "order",
        "filter": {"_id": oid},
        "projection": {"orderNumber": 1},
        "sort": [["orderNumber", -1]],
        "limit": 5,
    }


def test_count_aggregate_and_explain_use_their_own_endpoints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {"/count": {"count": 7}, "/aggregate": {"documents": [{"n": 1}]}, "/explain": {"explain": {"ok": 1}}}[
                request.url.path
            ]
        )

    repo, seen = build_repository(handler)

    assert repo.count("osco", "order", {"banner": "safeway"}) == 7
    assert repo.aggregate("osco", "order", [{"$match": {}}], 10) == [{"n": 1}]
    assert repo.explain_find("osco", "order", {}) == {"ok": 1}
    assert [r.url.path for r in seen] == ["/count", "/aggregate", "/explain"]
    assert json.loads(seen[1].content)["pipeline"] == [{"$match": {}}]


def test_ping_calls_health() -> None:
    repo, seen = build_repository(lambda request: httpx.Response(200))
    assert repo.ping() is True
    assert (seen[0].method, seen[0].url.path) == ("GET", "/health")


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(401, text="Unauthorized"), "HTTP 401"),
        (httpx.Response(500, text="boom"), "HTTP 500: boom"),
        (httpx.Response(200, text="not json"), "non-JSON"),
        (httpx.Response(200, json={"rows": []}), "'documents'"),
    ],
)
def test_api_failures_raise_data_api_error(response: httpx.Response, message: str) -> None:
    repo, _ = build_repository(lambda request: response)
    with pytest.raises(DataApiError, match=message):
        repo.find("osco", "order", {}, None, None, 1)


def test_network_failure_raises_data_api_error_without_leaking_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-host:1234 refused")

    repo, _ = build_repository(handler)
    with pytest.raises(DataApiError) as exc:
        repo.count("osco", "order", {})
    assert "secret-host" not in str(exc.value)


def test_api_mode_requires_base_url_and_mongodb_mode_requires_uri() -> None:
    with pytest.raises(ValueError, match="API_BASE_URL"):
        Settings(_env_file=None, DATA_ACCESS_MODE="api")
    with pytest.raises(ValueError, match="MONGODB_URI"):
        Settings(_env_file=None, DATA_ACCESS_MODE="mongodb")
    assert Settings(_env_file=None, DATA_ACCESS_MODE="api", API_BASE_URL="https://x.invalid").data_access_mode == "api"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"MONGODB_URI": "mongodb+srv://u:p@osco-prod-mongodb-01-pl-0.sk5vf.mongodb.net/"}, "prod"),
        ({"MONGODB_URI": "mongodb+srv://u:p@osco-qa2-mongodb-01.sk5vf.mongodb.net/"}, "nonprod"),
        ({"MONGODB_URI": "mongodb+srv://u:p@osco-nonprod-mongodb.sk5vf.mongodb.net/"}, "nonprod"),
        ({"MONGODB_URI": "mongodb+srv://u:p@osco-qa2.sk5vf.mongodb.net/", "OSCO_ENVIRONMENT": "prod"}, "prod"),
        ({"MONGODB_URI": None}, "unknown"),
    ],
)
def test_environment_is_inferred_from_mongodb_host(overrides: dict, expected: str) -> None:
    assert LocalTestSettings(**overrides).environment == expected
