class McpMongoError(Exception):
    """Base exception for user-facing MCP MongoDB errors."""


class CatalogError(McpMongoError):
    """Raised when schema catalog resources are missing or invalid."""


class UnknownDatabaseError(CatalogError):
    def __init__(self, database: str) -> None:
        super().__init__(f"Unknown database '{database}'. Add it to resources/databases.yaml first.")


class UnknownCollectionError(CatalogError):
    def __init__(self, database: str, collection: str) -> None:
        super().__init__(
            f"Unknown collection '{database}.{collection}'. Add it to the database registry and schema resources first."
        )


class DataApiError(McpMongoError):
    """Raised when the data API is unreachable, rejects the request, or returns an unusable response."""


class ValidationError(McpMongoError):
    """Raised when a query violates catalog or safety rules."""

