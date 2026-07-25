"""End-to-end test.

Starts a real StreamableHTTP MCP server that requires ``Authorization: Bearer
<secret>``, then runs the proxy as a subprocess over stdio (via the CLI's
``static-bearer`` strategy) and drives it with a real MCP client. This exercises
the full path: stdio JSON-RPC -> proxy -> authenticated HTTP hop -> upstream.

The AgentCore-specific token retrieval is covered by unit tests with a mocked
IdentityClient; here we validate the transport + credential-injection mechanics
against a live server.
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

SECRET = "super-secret-token"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_backend_app(secret: str):
    """Build a StreamableHTTP MCP app guarded by a bearer token."""
    mcp: FastMCP = FastMCP(name="backend")

    @mcp.tool
    def echo(text: str) -> str:
        """Return the input text unchanged."""
        return text

    @mcp.tool
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    inner = mcp.http_app(path="/mcp", transport="streamable-http", allowed_hosts=["*"], allowed_origins=["*"])

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            authorization = headers.get(b"authorization", b"").decode()
            if authorization != f"Bearer {secret}":
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await inner(scope, receive, send)

    return guarded, inner


class _BackgroundServer:
    def __init__(self, app, port: int):
        # The guarded wrapper doesn't carry a lifespan uvicorn can see, so pass the
        # inner app's lifespan via the config's lifespan hook.
        self._config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self, timeout: float = 15.0) -> None:
        self._thread.start()
        deadline = time.time() + timeout
        port = self._config.port
        while time.time() < deadline:
            if self._server.started:
                return
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    return
            time.sleep(0.1)
        raise RuntimeError("backend server did not start in time")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


@pytest.fixture
def backend():
    port = _free_port()
    guarded, inner = _build_backend_app(SECRET)
    # uvicorn drives the ASGI lifespan of the app object it is given; give it the
    # inner FastMCP app for lifespan while routing requests through the guard.
    app = _LifespanProxy(guarded, inner)
    server = _BackgroundServer(app, port)
    server.start()
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.stop()


class _LifespanProxy:
    """ASGI app that uses ``inner``'s lifespan but routes http/ws to ``guarded``."""

    def __init__(self, guarded, inner):
        self._guarded = guarded
        self._inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._inner(scope, receive, send)
        else:
            await self._guarded(scope, receive, send)


def _proxy_transport(endpoint: str, token: str) -> StdioTransport:
    return StdioTransport(
        command=sys.executable,
        args=[
            "-m",
            "mcp_identity_proxy_for_aws",
            endpoint,
            "--auth-type",
            "static-bearer",
            "--token",
            token,
            "--log-level",
            "WARNING",
        ],
        env=dict(os.environ),
    )


async def test_proxy_lists_and_calls_tools_with_valid_token(backend):
    client = Client(_proxy_transport(backend, SECRET))
    async with client:
        tools = {t.name for t in await client.list_tools()}
        assert {"echo", "add"} <= tools

        echoed = await client.call_tool("echo", {"text": "hello via proxy"})
        assert echoed.content[0].text == "hello via proxy"

        summed = await client.call_tool("add", {"a": 2, "b": 40})
        assert summed.content[0].text == "42"


async def test_proxy_fails_with_wrong_token(backend):
    client = Client(_proxy_transport(backend, "wrong-token"))
    raised = False
    try:
        async with client:
            await client.list_tools()
    except Exception:
        # Upstream returns 401; the failure must surface to the MCP client rather
        # than silently returning tools. The concrete exception type varies across
        # transport/proxy layers, so we only assert that an error propagated.
        raised = True
    assert raised, "expected an error when proxying with an invalid bearer token"
