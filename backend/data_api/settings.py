from __future__ import annotations

from pydantic import Field, model_validator

from backend.app.config import Settings


class DataApiSettings(Settings):
    """Settings for the data API service. This is the only process that holds MongoDB credentials."""

    data_api_username: str | None = Field(default=None, alias="DATA_API_USERNAME")
    data_api_password: str | None = Field(default=None, alias="DATA_API_PASSWORD")
    data_api_token: str | None = Field(default=None, alias="DATA_API_TOKEN")

    @property
    def basic_auth_configured(self) -> bool:
        return bool(self.data_api_username and self.data_api_password)

    @model_validator(mode="after")
    def validate_auth_configured(self) -> "DataApiSettings":
        # Fail closed: never start an unauthenticated API in front of the database.
        if not self.basic_auth_configured and not self.data_api_token:
            raise ValueError("Set DATA_API_USERNAME + DATA_API_PASSWORD and/or DATA_API_TOKEN to start the data API.")
        return self
