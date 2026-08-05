from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mongodb_uri: str | None = Field(default=None, alias="MONGODB_URI")
    mongodb_tls_ca_file: Path | None = Field(default=None, alias="MONGODB_TLS_CA_FILE")
    mcp_server_name: str = Field(default="mongodb-domain-mcp", alias="MCP_SERVER_NAME")
    mcp_read_only: bool = Field(default=True, alias="MCP_READ_ONLY")
    mcp_max_documents_per_query: int = Field(default=50, alias="MCP_MAX_DOCUMENTS_PER_QUERY")
    mcp_max_bytes_per_response: int = Field(default=1_000_000, alias="MCP_MAX_BYTES_PER_RESPONSE")
    mcp_schema_resource_dir: Path = Field(default=Path("backend/app/resources"), alias="MCP_SCHEMA_RESOURCE_DIR")
    mcp_log_level: str = Field(default="INFO", alias="MCP_LOG_LEVEL")
    mcp_allow_aggregate: bool = Field(default=True, alias="MCP_ALLOW_AGGREGATE")
    mcp_allow_write_tools: bool = Field(default=False, alias="MCP_ALLOW_WRITE_TOOLS")
    mcp_allow_pii_projection: bool = Field(default=False, alias="MCP_ALLOW_PII_PROJECTION")

    @model_validator(mode="after")
    def validate_runtime_settings(self) -> "Settings":
        if not self.mongodb_uri:
            raise ValueError("MONGODB_URI is required to start the MCP server.")
        if self.mcp_max_documents_per_query < 1:
            raise ValueError("MCP_MAX_DOCUMENTS_PER_QUERY must be at least 1.")
        if self.mcp_max_bytes_per_response < 1_000:
            raise ValueError("MCP_MAX_BYTES_PER_RESPONSE must be at least 1000.")
        if self.mongodb_tls_ca_file is not None and not self.mongodb_tls_ca_file.exists():
            self.mongodb_tls_ca_file = None
        return self


class LocalTestSettings(Settings):
    mongodb_uri: str | None = "mongodb://example.invalid:27017"
