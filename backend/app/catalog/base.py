from __future__ import annotations

from abc import ABC, abstractmethod

from backend.app.domain.schema_models import CollectionSchema, DatabaseDefinition


class SchemaCatalog(ABC):
    @abstractmethod
    def list_databases(self) -> list[DatabaseDefinition]:
        raise NotImplementedError

    @abstractmethod
    def list_collections(self, database: str) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def get_collection_schema(self, database: str, collection: str) -> CollectionSchema:
        raise NotImplementedError

    @abstractmethod
    def find_field_candidates(self, user_text: str) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def resolve_collection_candidates(self, user_text: str) -> list[dict]:
        raise NotImplementedError

