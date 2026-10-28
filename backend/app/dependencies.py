from __future__ import annotations

from dataclasses import dataclass

from backend.app.catalog.yaml_schema_catalog import YamlSchemaCatalog
from backend.app.config import Settings
from backend.app.db.api_repository import ApiRepository
from backend.app.db.mongo_client import MongoClientProvider
from backend.app.db.mongo_repository import MongoRepository
from backend.app.services.projection_policy import ProjectionPolicy
from backend.app.services.query_builder import QueryBuilder
from backend.app.services.query_validator import QueryValidator
from backend.app.services.response_sanitizer import ResponseSanitizer
from backend.app.services.target_resolver import TargetResolver


@dataclass(frozen=True)
class AppDependencies:
    settings: Settings
    catalog: YamlSchemaCatalog
    mongo_client_provider: MongoClientProvider | None
    repository: ApiRepository | MongoRepository
    target_resolver: TargetResolver
    query_validator: QueryValidator
    projection_policy: ProjectionPolicy
    response_sanitizer: ResponseSanitizer
    query_builder: QueryBuilder


def build_dependencies(settings: Settings | None = None) -> AppDependencies:
    resolved_settings = settings or Settings()
    catalog = YamlSchemaCatalog(resolved_settings.mcp_schema_resource_dir)
    mongo_client_provider: MongoClientProvider | None = None
    if resolved_settings.data_access_mode == "api":
        repository: ApiRepository | MongoRepository = ApiRepository(resolved_settings)
    else:
        mongo_client_provider = MongoClientProvider(resolved_settings)
        repository = MongoRepository(mongo_client_provider)
    return AppDependencies(
        settings=resolved_settings,
        catalog=catalog,
        mongo_client_provider=mongo_client_provider,
        repository=repository,
        target_resolver=TargetResolver(catalog),
        query_validator=QueryValidator(
            catalog=catalog,
            max_documents_per_query=resolved_settings.mcp_max_documents_per_query,
            allow_aggregate=resolved_settings.mcp_allow_aggregate,
        ),
        projection_policy=ProjectionPolicy(allow_pii_projection=resolved_settings.mcp_allow_pii_projection),
        response_sanitizer=ResponseSanitizer(max_bytes=resolved_settings.mcp_max_bytes_per_response),
        query_builder=QueryBuilder(),
    )

