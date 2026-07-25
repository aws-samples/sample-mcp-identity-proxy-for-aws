"""Static credential strategies (literal or environment-sourced values).

These do not use AgentCore Identity. They are useful for local testing, simple
deployments, or MCP servers guarded by a long-lived API key or bearer token.
"""

from __future__ import annotations

from mcp_identity_proxy_for_aws.auth.base import AuthStrategy
from mcp_identity_proxy_for_aws.credential import Credential, jwt_expiry


class StaticBearerStrategy(AuthStrategy):
    """Inject a fixed bearer token as ``Authorization: Bearer <token>``."""

    name = "static-bearer"

    def __init__(self, token: str, *, header_name: str = "Authorization", scheme: str | None = "Bearer") -> None:
        if not token:
            raise ValueError("static-bearer requires a non-empty token")
        self._token = token
        self._header_name = header_name
        self._scheme = scheme

    async def fetch(self, *, force_refresh: bool = False) -> Credential:
        return Credential(
            value=self._token,
            header_name=self._header_name,
            scheme=self._scheme,
            expires_at=jwt_expiry(self._token),
        )


class StaticApiKeyStrategy(AuthStrategy):
    """Inject a fixed API key into a configurable header.

    By default the key is placed verbatim into the ``x-api-key`` header (scheme
    ``None``). Set ``scheme="Bearer"`` and ``header_name="Authorization"`` to send
    the key as a bearer token instead.
    """

    name = "static-apikey"

    def __init__(self, api_key: str, *, header_name: str = "x-api-key", scheme: str | None = None) -> None:
        if not api_key:
            raise ValueError("static-apikey requires a non-empty api key")
        self._api_key = api_key
        self._header_name = header_name
        self._scheme = scheme

    async def fetch(self, *, force_refresh: bool = False) -> Credential:
        return Credential(
            value=self._api_key,
            header_name=self._header_name,
            scheme=self._scheme,
            expires_at=None,
        )
