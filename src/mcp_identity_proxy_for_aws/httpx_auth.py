"""httpx auth flow that injects credentials from an :class:`AuthStrategy`.

Adds a caching layer with three refresh triggers:
  1. JWT ``exp`` expiry (when the credential is a JWT), with a safety skew;
  2. a configurable time-to-live for opaque credentials with no known expiry;
  3. a ``401 Unauthorized`` response, which forces a fresh credential and one
     retry of the same request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Generator

import httpx

from mcp_identity_proxy_for_aws.auth.base import AuthStrategy
from mcp_identity_proxy_for_aws.credential import Credential

logger = logging.getLogger("mcp_identity_proxy_for_aws.httpx_auth")


class InjectingAuth(httpx.Auth):
    """Inject a credential from ``strategy`` into every outgoing request."""

    def __init__(self, strategy: AuthStrategy, *, token_ttl: float = 300.0) -> None:
        if token_ttl < 0:
            raise ValueError("token_ttl must be >= 0")
        self._strategy = strategy
        self._token_ttl = token_ttl
        self._cached: Credential | None = None
        self._fetched_at: float = 0.0
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        # Created lazily so the lock binds to the running event loop. The proxy
        # runs in a single event loop, so the ``None`` check is race-free.
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _needs_refresh(self, force: bool) -> bool:
        if force or self._cached is None:
            return True
        if self._cached.is_expired():
            return True
        # If the credential carries a known expiry (e.g. a JWT ``exp``), trust it
        # and skip the coarse TTL trigger — no need to re-fetch a still-valid token.
        if self._cached.expires_at is not None:
            return False
        if self._token_ttl and (time.monotonic() - self._fetched_at) >= self._token_ttl:
            return True
        return False

    async def _current(self, *, force: bool) -> Credential:
        async with self._get_lock():
            if self._needs_refresh(force):
                logger.debug("Fetching credential (force=%s) via %s", force, self._strategy.name)
                self._cached = await self._strategy.fetch(force_refresh=force)
                self._fetched_at = time.monotonic()
            assert self._cached is not None
            return self._cached

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        credential = await self._current(force=False)
        request.headers[credential.header_name] = credential.header_value
        response = yield request

        if response.status_code == 401:
            logger.info("Upstream returned 401; refreshing credential and retrying once.")
            credential = await self._current(force=True)
            request.headers[credential.header_name] = credential.header_value
            yield request

    def sync_auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        # The MCP StreamableHTTP transport is async-only; a sync client is never
        # used here. Provide a clear error rather than silently sending unauth'd.
        raise RuntimeError("InjectingAuth only supports async HTTP clients.")
