from __future__ import annotations

from typing import Any

from backend.app.db.mongo_client import MongoClientProvider


class MongoRepository:
    def __init__(self, client_provider: MongoClientProvider) -> None:
        self._client_provider = client_provider

    def ping(self) -> bool:
        return self._client_provider.ping()

    def list_database_names(self) -> list[str]:
        return self._client_provider.client.list_database_names()

    def list_collection_names(self, database: str) -> list[str]:
        return self._client_provider.client[database].list_collection_names()

    def find(
        self,
        database: str,
        collection: str,
        filter: dict[str, Any],
        projection: dict[str, int] | None,
        sort: list[tuple[str, int]] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        cursor = self._client_provider.client[database][collection].find(filter, projection)
        if sort:
            cursor = cursor.sort(sort)
        return list(cursor.limit(limit))

    def count(self, database: str, collection: str, filter: dict[str, Any]) -> int:
        return self._client_provider.client[database][collection].count_documents(filter)

    def aggregate(self, database: str, collection: str, pipeline: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        capped_pipeline = [*pipeline, {"$limit": limit}]
        return list(self._client_provider.client[database][collection].aggregate(capped_pipeline))

    def explain_find(self, database: str, collection: str, filter: dict[str, Any]) -> dict[str, Any]:
        return self._client_provider.client[database][collection].find(filter).explain()

