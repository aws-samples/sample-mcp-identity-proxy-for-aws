"""Tests for InjectingAuth: caching, TTL expiry, and 401-triggered refresh."""

from __future__ import annotations

import time

import httpx
import pytest

from mcp_identity_proxy_for_aws.auth.base import AuthStrategy
from mcp_identity_proxy_for_aws.credential import Credential
from mcp_identity_proxy_for_aws.httpx_auth import InjectingAuth


class CountingStrategy(AuthStrategy):
    """Returns an incrementing token; records how it was called."""

    name = "counting"

    def __init__(self, *, header_name="Authorization", scheme="Bearer", expires_at=None):
        self.calls = 0
        self.force_calls = 0
        self._header_name = header_name
        self._scheme = scheme
        self._expires_at = expires_at

    async def fetch(self, *, force_refresh: bool = False) -> Credential:
        self.calls += 1
        if force_refresh:
            self.force_calls += 1
        return Credential(
            value=f"token-{self.calls}",
            header_name=self._header_name,
            scheme=self._scheme,
            expires_at=self._expires_at,
        )


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://example.com/mcp")


def _response(status: int, request: httpx.Request) -> httpx.Response:
    resp = httpx.Response(status_code=status)
    resp.request = request
    return resp


async def _run_flow(auth: InjectingAuth, request: httpx.Request, statuses: list[int]) -> list[httpx.Headers]:
    """Drive an async auth flow, feeding responses for each yielded request.

    Returns a snapshot of the headers *at the moment each request is yielded*,
    because httpx reuses (mutates) the same Request object across the flow.
    Snapshots are case-insensitive httpx.Headers copies.
    """
    snapshots: list[httpx.Headers] = []
    gen = auth.async_auth_flow(request)
    sent = None
    idx = 0
    while True:
        try:
            req = await gen.asend(sent)
        except StopAsyncIteration:
            break
        snapshots.append(httpx.Headers(req.headers))
        status = statuses[idx] if idx < len(statuses) else 200
        sent = _response(status, req)
        idx += 1
    return snapshots


async def test_injects_header():
    strategy = CountingStrategy()
    auth = InjectingAuth(strategy, token_ttl=300)
    snapshots = await _run_flow(auth, _request(), [200])
    assert len(snapshots) == 1
    assert snapshots[0]["Authorization"] == "Bearer token-1"
    assert strategy.calls == 1


async def test_caches_across_requests():
    strategy = CountingStrategy(expires_at=None)
    auth = InjectingAuth(strategy, token_ttl=300)
    await _run_flow(auth, _request(), [200])
    await _run_flow(auth, _request(), [200])
    # Second request should reuse the cached credential.
    assert strategy.calls == 1


async def test_ttl_expiry_forces_refetch(monkeypatch):
    strategy = CountingStrategy()
    auth = InjectingAuth(strategy, token_ttl=100)

    clock = {"t": 1000.0}
    monkeypatch.setattr("mcp_identity_proxy_for_aws.httpx_auth.time.monotonic", lambda: clock["t"])

    await _run_flow(auth, _request(), [200])
    assert strategy.calls == 1
    # Advance beyond TTL.
    clock["t"] += 200
    await _run_flow(auth, _request(), [200])
    assert strategy.calls == 2


async def test_known_expiry_skips_ttl_refetch(monkeypatch):
    # A credential with a far-future expiry must NOT be re-fetched on TTL elapse.
    strategy = CountingStrategy(expires_at=time.time() + 3600)
    auth = InjectingAuth(strategy, token_ttl=100)

    clock = {"t": 1000.0}
    monkeypatch.setattr("mcp_identity_proxy_for_aws.httpx_auth.time.monotonic", lambda: clock["t"])

    await _run_flow(auth, _request(), [200])
    assert strategy.calls == 1
    clock["t"] += 10_000  # way past TTL, but token still valid by exp
    await _run_flow(auth, _request(), [200])
    assert strategy.calls == 1


def test_negative_ttl_rejected():
    with pytest.raises(ValueError, match="token_ttl must be >= 0"):
        InjectingAuth(CountingStrategy(), token_ttl=-1)


async def test_401_triggers_forced_refresh_and_retry():
    strategy = CountingStrategy()
    auth = InjectingAuth(strategy, token_ttl=300)
    snapshots = await _run_flow(auth, _request(), [401, 200])
    # First attempt with token-1, retried with a freshly forced token-2.
    assert len(snapshots) == 2
    assert snapshots[0]["Authorization"] == "Bearer token-1"
    assert snapshots[1]["Authorization"] == "Bearer token-2"
    assert strategy.force_calls == 1


async def test_api_key_header_raw_value():
    strategy = CountingStrategy(header_name="x-api-key", scheme=None)
    auth = InjectingAuth(strategy, token_ttl=300)
    snapshots = await _run_flow(auth, _request(), [200])
    assert snapshots[0]["x-api-key"] == "token-1"
    assert "Authorization" not in snapshots[0]


def test_sync_flow_rejected():
    auth = InjectingAuth(CountingStrategy(), token_ttl=300)
    with pytest.raises(RuntimeError):
        list(auth.sync_auth_flow(_request()))
