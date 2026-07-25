"""Tests for proxy transport building, especially the HTTPS credential guard."""

from __future__ import annotations

import logging

from mcp_identity_proxy_for_aws.config import ProxyConfig
from mcp_identity_proxy_for_aws.proxy import build_transport

WARN_LOGGER = "mcp_identity_proxy_for_aws.proxy"


def _cfg(endpoint: str, **kw) -> ProxyConfig:
    kw.setdefault("auth_type", "static-bearer")
    kw.setdefault("static_token", "secret-token")
    return ProxyConfig(endpoint=endpoint, **kw)


def _warned_cleartext(records) -> bool:
    return any("cleartext" in r.getMessage() for r in records)


def test_http_endpoint_warns_cleartext(caplog):
    with caplog.at_level(logging.WARNING, logger=WARN_LOGGER):
        build_transport(_cfg("http://gateway.example.com/mcp"))
    assert _warned_cleartext(caplog.records)


def test_https_endpoint_no_cleartext_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=WARN_LOGGER):
        build_transport(_cfg("https://gateway.example.com/mcp"))
    assert not _warned_cleartext(caplog.records)


def test_localhost_http_no_cleartext_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=WARN_LOGGER):
        build_transport(_cfg("http://127.0.0.1:8080/mcp"))
        build_transport(_cfg("http://localhost:8080/mcp"))
    assert not _warned_cleartext(caplog.records)


def test_auth_none_warns_unauthenticated(caplog):
    with caplog.at_level(logging.WARNING, logger=WARN_LOGGER):
        build_transport(ProxyConfig(endpoint="https://x/mcp", auth_type="none"))
    assert any("unauthenticated" in r.getMessage() for r in caplog.records)
