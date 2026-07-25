"""Configuration model for the proxy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AuthType = Literal[
    "agentcore-oauth",
    "agentcore-apikey",
    "agentcore-iam-jwt",
    "static-bearer",
    "static-apikey",
    "none",
]


@dataclass
class ProxyConfig:
    """All settings needed to build and run the proxy."""

    # -- transport / target --------------------------------------------------
    endpoint: str
    name: str = "mcp-identity-proxy-for-aws"
    headers: dict[str, str] = field(default_factory=dict)

    # -- auth selection ------------------------------------------------------
    auth_type: AuthType = "agentcore-oauth"
    header_name: str | None = None
    # "AUTO" -> use the per-auth-type default scheme. Any other value (including
    # "none"/"raw"/"" meaning no scheme) overrides it.
    scheme: str = "AUTO"

    # -- caching -------------------------------------------------------------
    token_ttl: float = 300.0

    # -- AWS / AgentCore -----------------------------------------------------
    region: str | None = None
    provider_name: str | None = None
    scopes: list[str] = field(default_factory=list)
    oauth_flow: Literal["M2M", "USER_FEDERATION", "ON_BEHALF_OF_TOKEN_EXCHANGE"] = "M2M"
    resources: list[str] = field(default_factory=list)
    audiences: list[str] = field(default_factory=list)
    callback_url: str | None = None
    workload_identity_name: str | None = None
    user_id: str | None = None
    user_token: str | None = None

    # -- IAM JWT -------------------------------------------------------------
    iam_jwt_audience: list[str] = field(default_factory=list)
    iam_jwt_signing_algorithm: str = "ES384"
    iam_jwt_duration_seconds: int = 300

    # -- static --------------------------------------------------------------
    static_token: str | None = None

    # -- logging -------------------------------------------------------------
    log_level: str = "INFO"
