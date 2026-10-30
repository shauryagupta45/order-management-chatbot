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


def test_order_history_inherits_order_fields() -> None:
    catalog = YamlSchemaCatalog(RESOURCE_DIR)
    order = catalog.get_collection_schema("osco", "order")
    history = catalog.get_collection_schema("osco", "orderHistory")

    # Inherited from order.yaml: same model, same field names.
    assert set(order.fields) <= set(history.fields)
    assert history.fields["banner"].filterable is True
    assert history.fields["subOrders.fulfillmentType"] == order.fields["subOrders.fulfillmentType"]
    # Overridden locally: history-specific descriptions and version fields.
    assert history.fields["orderCreatedDate"].type == "datetime"
    assert "late 2023" in history.fields["orderCreatedDate"].description
    assert history.fields["versionNumber"].sortable is True


def test_nested_inheritance_is_rejected(tmp_path: Path) -> None:
    resources = tmp_path / "resources"
    shutil.copytree(RESOURCE_DIR, resources)
    order_yaml = resources / "schemas/osco/order.yaml"
    order_yaml.write_text(
        "inherit_fields_from: schemas/osco/store.yaml\n" + order_yaml.read_text(encoding="utf-8"), encoding="utf-8"
    )

    with pytest.raises(CatalogError, match="only one level"):
        YamlSchemaCatalog(resources)
