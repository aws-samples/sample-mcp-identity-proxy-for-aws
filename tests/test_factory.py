"""Tests for the auth strategy factory."""

from __future__ import annotations

import pytest

from mcp_identity_proxy_for_aws.auth.agentcore import (
    AgentCoreApiKeyStrategy,
    AgentCoreOAuthStrategy,
)
from mcp_identity_proxy_for_aws.auth.factory import _resolve_scheme, build_strategy
from mcp_identity_proxy_for_aws.auth.iam_jwt import IamJwtStrategy
from mcp_identity_proxy_for_aws.auth.static import StaticApiKeyStrategy, StaticBearerStrategy
from mcp_identity_proxy_for_aws.config import ProxyConfig


@pytest.fixture(autouse=True)
def _no_boto(monkeypatch):
    # AgentCoreCredentialSource constructs an IdentityClient (boto3). Stub it so
    # factory tests stay hermetic and offline.
    monkeypatch.setattr(
        "mcp_identity_proxy_for_aws.auth.agentcore.IdentityClient",
        lambda region: object(),
    )


def _cfg(**kw) -> ProxyConfig:
    kw.setdefault("endpoint", "https://example.com/mcp")
    return ProxyConfig(**kw)


def test_none_returns_none():
    assert build_strategy(_cfg(auth_type="none")) is None


def test_static_bearer():
    s = build_strategy(_cfg(auth_type="static-bearer", static_token="abc"))
    assert isinstance(s, StaticBearerStrategy)


def test_static_bearer_requires_token():
    with pytest.raises(ValueError, match="static-bearer requires"):
        build_strategy(_cfg(auth_type="static-bearer"))


def test_static_apikey():
    s = build_strategy(_cfg(auth_type="static-apikey", static_token="key"))
    assert isinstance(s, StaticApiKeyStrategy)


def test_agentcore_oauth():
    s = build_strategy(_cfg(auth_type="agentcore-oauth", provider_name="p", scopes=["s"], region="us-east-1"))
    assert isinstance(s, AgentCoreOAuthStrategy)


def test_agentcore_oauth_requires_provider():
    with pytest.raises(ValueError, match="requires --provider-name"):
        build_strategy(_cfg(auth_type="agentcore-oauth"))


def test_agentcore_apikey():
    s = build_strategy(_cfg(auth_type="agentcore-apikey", provider_name="p", region="us-east-1"))
    assert isinstance(s, AgentCoreApiKeyStrategy)


def test_iam_jwt():
    s = build_strategy(_cfg(auth_type="agentcore-iam-jwt", iam_jwt_audience=["aud"], region="us-east-1"))
    assert isinstance(s, IamJwtStrategy)


def test_iam_jwt_requires_audience():
    with pytest.raises(ValueError, match="requires --iam-jwt-audience"):
        build_strategy(_cfg(auth_type="agentcore-iam-jwt"))


@pytest.mark.parametrize(
    "value,default,expected",
    [
        ("AUTO", "Bearer", "Bearer"),
        ("AUTO", None, None),
        ("none", "Bearer", None),
        ("raw", "Bearer", None),
        ("", "Bearer", None),
        ("Token", "Bearer", "Token"),
    ],
)
def test_resolve_scheme(value, default, expected):
    assert _resolve_scheme(value, default) == expected
