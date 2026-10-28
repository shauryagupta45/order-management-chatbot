from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import PyMongoError

from backend.app.db.api_repository import ApiRepository
from backend.app.dependencies import build_dependencies
from backend.app.domain.errors import DataApiError
from backend.data_api.main import create_app
from backend.data_api.settings import DataApiSettings

BASIC = {"Authorization": "Basic " + base64.b64encode(b"svc:pw").decode()}
BAD_BASIC = {"Authorization": "Basic " + base64.b64encode(b"svc:nope").decode()}


class FakeRepository:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.error: Exception | None = None

    def _maybe_fail(self) -> None:
        if self.error:
            raise self.error

    def ping(self) -> bool:
        self._maybe_fail()
        return True

    def find(self, database, collection, filter, projection, sort, limit):
        self._maybe_fail()
        self.calls.append(("find", database, collection, filter, projection, sort, limit))
        return [{"_id": ObjectId("507f1f77bcf86cd799439011"), "orderNumber": "1"}]

    def count(self, database, collection, filter):
        self.calls.append(("count", database, collection, filter))
        return 3

    def aggregate(self, database, collection, pipeline, limit):
        self.calls.append(("aggregate", database, collection, pipeline, limit))
        return [{"_id": None, "count": 3}]

    def explain_find(self, database, collection, filter):
        return {"queryPlanner": {"winningPlan": {"stage": "IXSCAN"}}}


def make_settings(**overrides) -> DataApiSettings:
    values = {
        "DATA_ACCESS_MODE": "mongodb",
        "MONGODB_URI": "mongodb://example.invalid:27017",
        "MCP_SCHEMA_RESOURCE_DIR": Path("backend/app/resources"),
        "DATA_API_USERNAME": "svc",
        "DATA_API_PASSWORD": "pw",
        **overrides,
    }
    return DataApiSettings(_env_file=None, **values)


@pytest.fixture
def repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def client(repo: FakeRepository) -> TestClient:
    settings = make_settings()
    app = create_app(settings, replace(build_dependencies(settings), repository=repo))
    return TestClient(app, headers=BASIC)


def test_requires_credentials(client: TestClient) -> None:
    anonymous = TestClient(client.app)
    assert anonymous.post("/count", json={"database": "osco", "collection": "order"}).status_code == 401
    assert client.post("/count", json={"database": "osco", "collection": "order"}, headers=BAD_BASIC).status_code == 401
    assert anonymous.get("/health").status_code == 401


def test_bearer_token_is_accepted_when_configured(repo: FakeRepository) -> None:
    settings = make_settings(DATA_API_USERNAME=None, DATA_API_PASSWORD=None, DATA_API_TOKEN="t0ken")
    app = create_app(settings, replace(build_dependencies(settings), repository=repo))
    bearer = TestClient(app, headers={"Authorization": "Bearer t0ken"})
    assert bearer.get("/health").json() == {"ok": True}
    assert TestClient(app, headers={"Authorization": "Bearer wrong"}).get("/health").status_code == 401


def test_refuses_to_start_without_any_auth() -> None:
    with pytest.raises(ValueError, match="DATA_API_USERNAME"):
        make_settings(DATA_API_USERNAME=None, DATA_API_PASSWORD=None)


def test_find_validates_then_queries(client: TestClient, repo: FakeRepository) -> None:
    response = client.post(
        "/find",
        json={"database": "osco", "collection": "order", "filter": {"orderNumber": "1"},
              "projection": {"orderNumber": 1}, "sort": [["orderTotal.amount", -1]], "limit": 500},
    )
    assert response.status_code == 200
    assert response.json() == {"documents": [{"_id": {"$oid": "507f1f77bcf86cd799439011"}, "orderNumber": "1"}]}
    _, database, collection, filter_, projection, sort, limit = repo.calls[0]
    assert (database, collection, filter_, projection, sort) == ("osco", "order", {"orderNumber": "1"}, {"orderNumber": 1}, [("orderTotal.amount", -1)])
    assert limit <= 50  # capped by MCP_MAX_DOCUMENTS_PER_QUERY


@pytest.mark.parametrize(
    ("path", "body", "message"),
    [
        ("/find", {"database": "osco", "collection": "order", "filter": {"nope": 1}}, "Unknown filter field"),
        ("/find", {"database": "osco", "collection": "order", "filter": {"$where": "1"}}, "not allowed"),
        ("/find", {"database": "osco", "collection": "order", "projection": {"nope": 1}}, "Unknown projection field"),
        ("/find", {"database": "osco", "collection": "order", "sort": [["nope", 1]]}, "Unknown sort field"),
        ("/find", {"database": "nope", "collection": "order"}, "Unknown database"),
        ("/aggregate", {"database": "osco", "collection": "order", "pipeline": [{"$out": "x"}]}, "not allowed"),
    ],
)
def test_invalid_queries_are_rejected_before_reaching_the_database(
    client: TestClient, repo: FakeRepository, path: str, body: dict, message: str
) -> None:
    response = client.post(path, json=body)
    assert response.status_code == 400
    assert response.json()["status"] == "BAD_REQUEST"
    assert message in response.json()["message"]
    assert repo.calls == []


def test_unknown_request_fields_are_rejected(client: TestClient) -> None:
    response = client.post("/find", json={"database": "osco", "collection": "order", "writeConcern": 1})
    assert response.status_code == 422


def test_database_failure_returns_502_without_details(client: TestClient, repo: FakeRepository) -> None:
    repo.error = PyMongoError("mongodb+srv://user:secret@host failed")
    response = client.post("/find", json={"database": "osco", "collection": "order"})
    assert response.status_code == 502
    assert "secret" not in response.text


def test_there_is_no_write_endpoint(client: TestClient) -> None:
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)("/find").status_code in (404, 405)


def test_api_repository_client_works_against_this_server(client: TestClient, repo: FakeRepository) -> None:
    """Contract check: the MCP-side ApiRepository and this server agree, including ObjectId round-tripping."""
    oid = ObjectId("507f1f77bcf86cd799439011")
    api = ApiRepository(make_settings(API_BASE_URL="http://testserver"), client=client)

    assert api.ping() is True
    assert api.find("osco", "order", {"orderNumber": "1"}, {"orderNumber": 1}, None, 5) == [{"_id": oid, "orderNumber": "1"}]
    assert api.count("osco", "order", {"orderNumber": "1"}) == 3
    assert api.aggregate("osco", "order", [{"$group": {"_id": None, "count": {"$sum": 1}}}], 10) == [{"_id": None, "count": 3}]
    assert api.explain_find("osco", "order", {})["queryPlanner"]["winningPlan"]["stage"] == "IXSCAN"

    with pytest.raises(DataApiError, match="HTTP 400"):
        api.find("osco", "order", {"nope": 1}, None, None, 1)
