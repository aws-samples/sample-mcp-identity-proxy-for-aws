"""End-to-end test for API-key injection.

Starts a StreamableHTTP MCP server that requires ``x-api-key: <key>``, then runs
the proxy as a subprocess over stdio using the ``static-apikey`` strategy and
drives it with a real MCP client. This covers the API-key injection path in CI
without needing AWS. (The ``agentcore-apikey`` strategy shares the same injection
code path; only the key *source* differs and is unit-tested with a mocked
IdentityClient in ``test_agentcore.py``.)
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time

import pytest
import uvicorn
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport

API_KEY = "test-api-key-abc123"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_app(key: str):
    mcp: FastMCP = FastMCP(name="apikey-backend")

    @mcp.tool
    def echo(text: str) -> str:
        """Return the input text unchanged."""
        return text

    inner = mcp.http_app(path="/mcp", transport="streamable-http", allowed_hosts=["*"], allowed_origins=["*"])

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            await inner(scope, receive, send)
            return
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if headers.get(b"x-api-key", b"").decode() != key:
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"invalid api key"})
                return
        await inner(scope, receive, send)

    return app


class _BackgroundServer:
    def __init__(self, app, port: int):
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._port = port

    def start(self, timeout: float = 15.0) -> None:
        self._thread.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._server.started:
                return
            with socket.socket() as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", self._port)) == 0:
                    return
            time.sleep(0.1)
        raise RuntimeError("backend did not start")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


@pytest.fixture
def backend():
    port = _free_port()
    server = _BackgroundServer(_build_app(API_KEY), port)
    server.start()
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.stop()


def _transport(endpoint: str, key: str) -> StdioTransport:
    return StdioTransport(
        command=sys.executable,
        args=[
            "-m", "mcp_identity_proxy_for_aws", endpoint,
            "--auth-type", "static-apikey",
            "--token", key,
            "--header-name", "x-api-key",
            "--log-level", "WARNING",
        ],
        env=dict(os.environ),
    )


async def test_apikey_injection_valid_key(backend):
    async with Client(_transport(backend, API_KEY)) as client:
        tools = {t.name for t in await client.list_tools()}
        assert "echo" in tools
        result = await client.call_tool("echo", {"text": "api key works"})
        assert result.content[0].text == "api key works"


async def test_apikey_injection_wrong_key(backend):
    raised = False
    try:
        async with Client(_transport(backend, "wrong-key")) as client:
            await client.list_tools()
    except Exception:
        raised = True
    assert raised, "expected failure when injecting an invalid api key"
