from __future__ import annotations

import certifi
from pymongo import MongoClient

from backend.app.config import Settings


class MongoClientProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: MongoClient | None = None

    @property
    def client(self) -> MongoClient:
        if self._client is None:
            kwargs = {
                "serverSelectionTimeoutMS": 5_000,
                "appname": self._settings.mcp_server_name,
            }
            if self._settings.mongodb_tls_ca_file is not None:
                kwargs["tlsCAFile"] = str(self._settings.mongodb_tls_ca_file)
            else:
                kwargs["tlsCAFile"] = certifi.where()
            self._client = MongoClient(
                self._settings.mongodb_uri,
                **kwargs,
            )
        return self._client

    def ping(self) -> bool:
        self.client.admin.command("ping")
        return True

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
