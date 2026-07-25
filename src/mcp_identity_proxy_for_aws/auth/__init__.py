"""Pluggable authentication strategies for mcp-identity-proxy-for-aws."""

from mcp_identity_proxy_for_aws.auth.agentcore import (
    AgentCoreApiKeyStrategy,
    AgentCoreCredentialSource,
    AgentCoreOAuthStrategy,
)
from mcp_identity_proxy_for_aws.auth.base import AuthStrategy
from mcp_identity_proxy_for_aws.auth.factory import build_strategy
from mcp_identity_proxy_for_aws.auth.iam_jwt import IamJwtStrategy
from mcp_identity_proxy_for_aws.auth.static import StaticApiKeyStrategy, StaticBearerStrategy

__all__ = [
    "AuthStrategy",
    "AgentCoreOAuthStrategy",
    "AgentCoreApiKeyStrategy",
    "AgentCoreCredentialSource",
    "IamJwtStrategy",
    "StaticBearerStrategy",
    "StaticApiKeyStrategy",
    "build_strategy",
]
