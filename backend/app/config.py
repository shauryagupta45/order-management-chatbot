from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # "api" sends validated queries to the data API; "mongodb" connects to MongoDB directly.
    data_access_mode: Literal["api", "mongodb"] = Field(default="api", alias="DATA_ACCESS_MODE")
    api_base_url: str | None = Field(default=None, alias="API_BASE_URL")
    api_token: str | None = Field(default=None, alias="API_TOKEN")
    api_username: str | None = Field(default=None, alias="API_USERNAME")
    api_password: str | None = Field(default=None, alias="API_PASSWORD")
    api_timeout_seconds: float = Field(default=60.0, alias="API_TIMEOUT_SECONDS")
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
    # "prod" or "nonprod". Leave unset to infer it from the MONGODB_URI host (e.g. "osco-prod-mongodb-01...").
    osco_environment: Literal["prod", "nonprod"] | None = Field(default=None, alias="OSCO_ENVIRONMENT")

    @property
    def environment(self) -> Literal["prod", "nonprod", "unknown"]:
        if self.osco_environment:
            return self.osco_environment
        if not self.mongodb_uri:
            return "unknown"
        host = urlsplit(self.mongodb_uri).hostname or ""
        return "prod" if re.search(r"(^|[-.])prod([-.]|$)", host) else "nonprod"

    @model_validator(mode="after")
    def validate_runtime_settings(self) -> "Settings":
        if self.data_access_mode == "api" and not self.api_base_url:
            raise ValueError("API_BASE_URL is required when DATA_ACCESS_MODE=api.")
        if self.data_access_mode == "mongodb" and not self.mongodb_uri:
            raise ValueError("MONGODB_URI is required when DATA_ACCESS_MODE=mongodb.")
        if self.mcp_max_documents_per_query < 1:
            raise ValueError("MCP_MAX_DOCUMENTS_PER_QUERY must be at least 1.")
        if self.mcp_max_bytes_per_response < 1_000:
            raise ValueError("MCP_MAX_BYTES_PER_RESPONSE must be at least 1000.")
        if self.mongodb_tls_ca_file is not None and not self.mongodb_tls_ca_file.exists():
            self.mongodb_tls_ca_file = None
        return self


class LocalTestSettings(Settings):
    api_base_url: str | None = "https://data-api.example.invalid"
    mongodb_uri: str | None = "mongodb://example.invalid:27017"
