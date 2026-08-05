from __future__ import annotations

from backend.app.dependencies import AppDependencies


def register_schema_tools(mcp, dependencies: AppDependencies) -> None:
    @mcp.tool()
    def list_databases_context() -> dict:
        """Use this before querying to list curated databases available through this MCP server."""
        return {
            "databases": [
                database.model_dump(include={"name", "display_name", "description", "aliases"})
                for database in dependencies.catalog.list_databases()
            ]
        }

    @mcp.tool()
    def list_collections_context(database: str) -> dict:
        """Use this to list curated collections for a configured database name."""
        definition = next(item for item in dependencies.catalog.list_databases() if item.name == database)
        return {"database": database, "collections": [collection.model_dump() for collection in definition.collections]}

    @mcp.tool()
    def get_collection_context(database: str, collection: str) -> dict:
        """Use this to inspect field metadata, PII policy, examples, and business context before querying."""
        schema = dependencies.catalog.get_collection_schema(database, collection)
        return schema.model_dump()

    @mcp.tool()
    def search_schema_context(user_request: str) -> dict:
        """Use this first for natural-language requests to find relevant databases, collections, and fields."""
        return {"matches": dependencies.catalog.search(user_request)}

    @mcp.tool()
    def resolve_query_target(user_request: str) -> dict:
        """Use this to resolve a user request into the best database, collection, and candidate fields."""
        return dependencies.target_resolver.resolve(user_request).model_dump()

