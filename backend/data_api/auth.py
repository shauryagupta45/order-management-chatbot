from __future__ import annotations

import secrets
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer

from backend.data_api.settings import DataApiSettings


def _matches(supplied: str, expected: str) -> bool:
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def build_authenticator(settings: DataApiSettings) -> Callable[..., None]:
    """Return a FastAPI dependency that accepts Basic auth and/or a bearer token, whichever is configured."""
    basic = HTTPBasic(auto_error=False)
    bearer = HTTPBearer(auto_error=False)

    def authenticate(
        basic_credentials: HTTPBasicCredentials | None = Depends(basic),
        bearer_credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> None:
        if bearer_credentials and settings.data_api_token and _matches(bearer_credentials.credentials, settings.data_api_token):
            return
        if basic_credentials and settings.basic_auth_configured:
            # Evaluate both comparisons so timing doesn't reveal which one failed.
            user_ok = _matches(basic_credentials.username, settings.data_api_username)
            password_ok = _matches(basic_credentials.password, settings.data_api_password)
            if user_ok and password_ok:
                return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )

    return authenticate
