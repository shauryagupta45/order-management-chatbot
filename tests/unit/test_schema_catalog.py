from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.app.catalog.yaml_schema_catalog import YamlSchemaCatalog
from backend.app.domain.errors import CatalogError, UnknownCollectionError, UnknownDatabaseError


RESOURCE_DIR = Path("backend/app/resources")


def test_yaml_catalog_loads_databases_and_schemas() -> None:
    catalog = YamlSchemaCatalog(RESOURCE_DIR)

    assert [database.name for database in catalog.list_databases()] == ["orderff", "osco"]
    assert "shipment" in catalog.list_collections("orderff")
    assert "orderSummary" in catalog.list_collections("osco")
    assert catalog.get_collection_schema("osco", "order").fields["_id"].filterable is True


def test_missing_schema_file_fails_fast(tmp_path: Path) -> None:
    resources = tmp_path / "resources"
    shutil.copytree(RESOURCE_DIR, resources)
    databases_yaml = resources / "databases.yaml"
    databases_yaml.write_text(
        databases_yaml.read_text(encoding="utf-8").replace(
            "schemas/orderff/batchjob.yaml", "schemas/orderff/missing.yaml"
        ),
        encoding="utf-8",
    )

    with pytest.raises(CatalogError, match="Missing schema resource file"):
        YamlSchemaCatalog(resources)


def test_invalid_database_name() -> None:
    catalog = YamlSchemaCatalog(RESOURCE_DIR)

    with pytest.raises(UnknownDatabaseError, match="Unknown database"):
        catalog.list_collections("missing")


def test_invalid_collection_name() -> None:
    catalog = YamlSchemaCatalog(RESOURCE_DIR)

    with pytest.raises(UnknownCollectionError, match="Unknown collection"):
        catalog.get_collection_schema("orderff", "missing")
