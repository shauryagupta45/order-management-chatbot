from __future__ import annotations

from typing import Any

import httpx
from bson import json_util

from backend.app.config import Settings
from backend.app.domain.errors import DataApiError


class ApiRepository:
    """Runs the already-validated MongoDB queries through an HTTP API instead of a direct DB connection.

    Drop-in replacement for MongoRepository: the MCP tools build and validate the query exactly as before, and
    this class only transports it. Each call POSTs the query as JSON to `<API_BASE_URL>/<operation>`:

        POST /find       {database, collection, filter, projection, sort, limit}  -> {"documents": [...]}
        POST /count      {database, collection, filter}                           -> {"count": <int>}
        POST /aggregate  {database, collection, pipeline, limit}                  -> {"documents": [...]}
        POST /explain    {database, collection, filter}                           -> {"explain": {...}}
        GET  /health                                                              -> any 2xx

    Bodies use MongoDB Extended JSON so ObjectId / date / Decimal128 values survive the round trip. `sort` is sent
    as a list of [field, direction] pairs. The API is expected to run the query read-only.
    """

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.api_base_url:
            raise ValueError("API_BASE_URL is required when DATA_ACCESS_MODE=api.")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if settings.api_token:
            headers["Authorization"] = f"Bearer {settings.api_token}"
        auth = None
        if settings.api_username and settings.api_password:
            auth = httpx.BasicAuth(settings.api_username, settings.api_password)
        self._client = client or httpx.Client(
            base_url=settings.api_base_url.rstrip("/"),
            headers=headers,
            auth=auth,
            timeout=settings.api_timeout_seconds,
        )

    def ping(self) -> bool:
        self._request("GET", "/health")
        return True

    def find(
        self,
        database: str,
        collection: str,
        filter: dict[str, Any],
        projection: dict[str, int] | None,
        sort: list[tuple[str, int]] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        body = {
            "database": database,
            "collection": collection,
            "filter": filter,
            "projection": projection,
            "sort": [list(pair) for pair in sort] if sort else None,
            "limit": limit,
        }
        return self._documents(self._request("POST", "/find", body))

    def count(self, database: str, collection: str, filter: dict[str, Any]) -> int:
        data = self._request("POST", "/count", {"database": database, "collection": collection, "filter": filter})
        if not isinstance(data, dict) or "count" not in data:
            raise DataApiError("Data API /count response is missing 'count'.")
        return int(data["count"])

    def aggregate(self, database: str, collection: str, pipeline: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        body = {"database": database, "collection": collection, "pipeline": pipeline, "limit": limit}
        return self._documents(self._request("POST", "/aggregate", body))

    def explain_find(self, database: str, collection: str, filter: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", "/explain", {"database": database, "collection": collection, "filter": filter})
        if not isinstance(data, dict) or "explain" not in data:
            raise DataApiError("Data API /explain response is missing 'explain'.")
        return data["explain"]

    def close(self) -> None:
        self._client.close()

    # ── transport ────────────────────────────────────────────────────────────

    @staticmethod
    def _documents(data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
            raise DataApiError("Data API response is missing a 'documents' list.")
        return data["documents"]

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.request(
                method,
                path,
                content=json_util.dumps(body) if body is not None else None,
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise DataApiError(f"Data API {method} {path} failed: {exc.__class__.__name__}") from exc

        if response.status_code == 401:
            raise DataApiError("Data API rejected the credentials (HTTP 401).")
        if response.status_code >= 400:
            raise DataApiError(f"Data API {method} {path} returned HTTP {response.status_code}: {response.text[:300]}")
        if not response.content:
            return None
        try:
            return json_util.loads(response.text)
        except ValueError as exc:
            raise DataApiError(f"Data API {method} {path} returned a non-JSON response.") from exc
