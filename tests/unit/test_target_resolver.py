from __future__ import annotations

from pathlib import Path

from backend.app.catalog.yaml_schema_catalog import YamlSchemaCatalog
from backend.app.services.target_resolver import TargetResolver


def test_collection_resolution_for_osco_order_summary() -> None:
    resolver = TargetResolver(YamlSchemaCatalog(Path("backend/app/resources")))

    target = resolver.resolve("show osco order summary records")

    assert target.database == "osco"
    assert target.collection == "orderSummary"
    assert target.confidence == "high"


def test_collection_resolution_for_orderff_picking_status() -> None:
    resolver = TargetResolver(YamlSchemaCatalog(Path("backend/app/resources")))

    target = resolver.resolve("find orderff picking status")

    assert target.database == "orderff"
    assert target.collection == "order_picking_status"
