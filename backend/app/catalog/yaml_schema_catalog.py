from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from backend.app.catalog.base import SchemaCatalog
from backend.app.catalog.schema_index import SchemaIndex
from backend.app.domain.errors import CatalogError, UnknownCollectionError, UnknownDatabaseError
from backend.app.domain.schema_models import CollectionSchema, DatabaseCatalog, DatabaseDefinition



class YamlSchemaCatalog(SchemaCatalog):
    def __init__(self, resource_dir: str | Path) -> None:
        self.resource_dir = Path(resource_dir)
        self._catalog = self._load_catalog()
        self._schemas: dict[tuple[str, str], CollectionSchema] = {}
        self._load_all_schemas()
        self._index = SchemaIndex(self.list_databases(), list(self._schemas.values()))

    def list_databases(self) -> list[DatabaseDefinition]:
        return self._catalog.databases

    def list_collections(self, database: str) -> list[str]:
        definition = self._get_database_definition(database)
        return [collection.name for collection in definition.collections]

    def get_collection_schema(self, database: str, collection: str) -> CollectionSchema:
        self._get_database_definition(database)
        schema = self._schemas.get((database, collection))
        if schema is None:
            raise UnknownCollectionError(database, collection)
        return schema

    def find_field_candidates(self, user_text: str) -> list[dict]:
        return self._index.search(user_text, kind="field")

    def resolve_collection_candidates(self, user_text: str) -> list[dict]:
        return self._index.search(user_text, kind="collection")

    def search(self, user_text: str, limit: int = 10) -> list[dict]:
        return self._index.search(user_text, limit=limit)

    def _load_catalog(self) -> DatabaseCatalog:
        path = self.resource_dir / "databases.yaml"
        data = self._read_yaml(path)
        try:
            return DatabaseCatalog.model_validate(data)
        except PydanticValidationError as exc:
            raise CatalogError(f"Invalid database catalog '{path}': {exc}") from exc

    def _load_all_schemas(self) -> None:
        for database in self._catalog.databases:
            seen_collections: set[str] = set()
            for collection in database.collections:
                if collection.name in seen_collections:
                    raise CatalogError(f"Duplicate collection '{database.name}.{collection.name}' in databases.yaml")
                seen_collections.add(collection.name)
                path = self.resource_dir / collection.schema_file
                schema = self._load_schema(path)
                if schema.database != database.name or schema.collection != collection.name:
                    raise CatalogError(
                        f"Schema '{path}' declares {schema.database}.{schema.collection}, "
                        f"but registry expects {database.name}.{collection.name}."
                    )
                self._schemas[(database.name, collection.name)] = schema

    def _load_schema(self, path: Path) -> CollectionSchema:
        data = self._read_yaml(path)
        parent = data.pop("inherit_fields_from", None)
        if parent:
            # Collections that store the same document model (e.g. osco.orderHistory archives osco.order) reuse the
            # parent's field metadata; fields declared locally override or extend the inherited ones.
            parent_data = self._read_yaml(self.resource_dir / parent)
            if "inherit_fields_from" in parent_data:
                raise CatalogError(f"Schema '{path}' inherits from '{parent}', which itself inherits; only one level is supported.")
            data["fields"] = {**parent_data.get("fields", {}), **(data.get("fields") or {})}
        try:
            return CollectionSchema.model_validate(data)
        except PydanticValidationError as exc:
            raise CatalogError(f"Invalid collection schema '{path}': {exc}") from exc

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise CatalogError(f"Missing schema resource file: {path}")
        try:
            with path.open("r", encoding="utf-8") as file:
                data = yaml.safe_load(file)
        except yaml.YAMLError as exc:
            raise CatalogError(f"Malformed YAML in '{path}': {exc}") from exc
        if not isinstance(data, dict):
            raise CatalogError(f"YAML file '{path}' must contain an object at the top level.")
        return data

    def _get_database_definition(self, database: str) -> DatabaseDefinition:
        for definition in self._catalog.databases:
            if definition.name == database:
                return definition
        raise UnknownDatabaseError(database)
