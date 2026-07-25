"""Proxy core: build a FastMCP proxy that forwards stdio <-> authenticated HTTP."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import fastmcp
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import ProxyClient

from mcp_identity_proxy_for_aws.auth.factory import build_strategy
from mcp_identity_proxy_for_aws.config import ProxyConfig
from mcp_identity_proxy_for_aws.httpx_auth import InjectingAuth

logger = logging.getLogger("mcp_identity_proxy_for_aws.proxy")

# Silence FastMCP deprecation chatter that could otherwise reach stderr noisily.
fastmcp.settings.deprecation_warnings = False

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def build_transport(config: ProxyConfig) -> StreamableHttpTransport:
    """Build the authenticated StreamableHTTP transport to the upstream MCP server."""
    strategy = build_strategy(config)
    auth = InjectingAuth(strategy, token_ttl=config.token_ttl) if strategy is not None else None
    if auth is None:
        logger.warning("Auth is disabled (auth_type=none); requests will be sent unauthenticated.")
    else:
        parsed = urlparse(config.endpoint)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" and host not in _LOCAL_HOSTS:
            logger.warning(
                "Endpoint %s is not HTTPS: the injected credential will be sent in cleartext. "
                "Use an https:// endpoint in production.",
                config.endpoint,
            )
    return StreamableHttpTransport(
        url=config.endpoint,
        headers=config.headers or None,
        auth=auth,
    )


def build_proxy(config: ProxyConfig):
    """Build a FastMCP proxy server for ``config``.

    The returned server exposes the upstream MCP server's tools, resources and
    prompts locally over stdio, injecting the configured credential into every
    upstream HTTP request.
    """
    transport = build_transport(config)
    backend = ProxyClient(transport)
    proxy = create_proxy(backend, name=config.name)
    logger.info(
        "Proxy ready: name=%s endpoint=%s auth=%s",
        config.name,
        config.endpoint,
        config.auth_type,
    )
    return proxy


def run_proxy(config: ProxyConfig) -> None:
    """Build and run the proxy over stdio (blocking)."""
    proxy = build_proxy(config)
    # stdio is the default transport; disable the banner so stdout stays clean
    # for JSON-RPC framing.
    proxy.run(show_banner=False)
