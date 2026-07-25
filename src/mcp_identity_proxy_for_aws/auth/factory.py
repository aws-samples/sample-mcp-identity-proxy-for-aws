"""Factory that builds an :class:`AuthStrategy` from a :class:`ProxyConfig`."""

from __future__ import annotations

from mcp_identity_proxy_for_aws.auth.agentcore import (
    AgentCoreApiKeyStrategy,
    AgentCoreCredentialSource,
    AgentCoreOAuthStrategy,
    resolve_region,
)
from mcp_identity_proxy_for_aws.auth.base import AuthStrategy
from mcp_identity_proxy_for_aws.auth.iam_jwt import IamJwtStrategy
from mcp_identity_proxy_for_aws.auth.static import StaticApiKeyStrategy, StaticBearerStrategy
from mcp_identity_proxy_for_aws.config import ProxyConfig


def _resolve_scheme(config_scheme: str, default: str | None) -> str | None:
    """Resolve the auth scheme, honouring the ``AUTO`` sentinel and raw overrides."""
    if config_scheme == "AUTO":
        return default
    if config_scheme.lower() in ("", "none", "raw"):
        return None
    return config_scheme


def build_strategy(config: ProxyConfig) -> AuthStrategy | None:
    """Build the auth strategy for ``config``, or ``None`` when auth is disabled.

    Raises:
        ValueError: if the selected auth type is missing required parameters.
    """
    auth_type = config.auth_type

    if auth_type == "none":
        return None

    if auth_type == "static-bearer":
        token = config.static_token
        if not token:
            raise ValueError("static-bearer requires --token (or PROXY_STATIC_TOKEN).")
        return StaticBearerStrategy(
            token,
            header_name=config.header_name or "Authorization",
            scheme=_resolve_scheme(config.scheme, "Bearer"),
        )

    if auth_type == "static-apikey":
        token = config.static_token
        if not token:
            raise ValueError("static-apikey requires --token (or PROXY_STATIC_TOKEN).")
        return StaticApiKeyStrategy(
            token,
            header_name=config.header_name or "x-api-key",
            scheme=_resolve_scheme(config.scheme, None),  # default: raw value
        )

    if auth_type == "agentcore-iam-jwt":
        if not config.iam_jwt_audience:
            raise ValueError("agentcore-iam-jwt requires --iam-jwt-audience.")
        return IamJwtStrategy(
            region=resolve_region(config.region),
            audience=config.iam_jwt_audience,
            signing_algorithm=config.iam_jwt_signing_algorithm,
            duration_seconds=config.iam_jwt_duration_seconds,
            header_name=config.header_name or "Authorization",
            scheme=_resolve_scheme(config.scheme, "Bearer"),
        )

    if auth_type in ("agentcore-oauth", "agentcore-apikey"):
        if not config.provider_name:
            raise ValueError(f"{auth_type} requires --provider-name.")
        source = AgentCoreCredentialSource(
            region=resolve_region(config.region),
            workload_identity_name=config.workload_identity_name,
            user_id=config.user_id,
            user_token=config.user_token,
        )
        if auth_type == "agentcore-oauth":
            return AgentCoreOAuthStrategy(
                source=source,
                provider_name=config.provider_name,
                scopes=config.scopes,
                auth_flow=config.oauth_flow,
                resources=config.resources or None,
                audiences=config.audiences or None,
                callback_url=config.callback_url,
                header_name=config.header_name or "Authorization",
                scheme=_resolve_scheme(config.scheme, "Bearer"),
            )
        return AgentCoreApiKeyStrategy(
            source=source,
            provider_name=config.provider_name,
            header_name=config.header_name or "x-api-key",
            scheme=_resolve_scheme(config.scheme, None),  # default: raw value
        )

    raise ValueError(f"Unknown auth type: {auth_type}")
