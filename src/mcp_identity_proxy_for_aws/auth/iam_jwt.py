"""AWS IAM JWT strategy.

Obtains an AWS-signed JWT via STS ``GetWebIdentityToken`` and injects it as a
bearer token. This is useful for MCP servers / AgentCore Gateways configured to
trust your AWS account as an OIDC issuer -- no client secret is required, the JWT
is signed by AWS.

Requires AWS IAM Outbound Web Identity Federation to be enabled for the account
and ``sts:GetWebIdentityToken`` permission on the caller.
"""

from __future__ import annotations

import asyncio

import boto3
from botocore.exceptions import ClientError

from mcp_identity_proxy_for_aws.auth.base import AuthStrategy
from mcp_identity_proxy_for_aws.credential import Credential, jwt_expiry


class IamJwtStrategy(AuthStrategy):
    """Fetch an AWS-signed JWT from STS and inject it as a bearer token."""

    name = "agentcore-iam-jwt"

    def __init__(
        self,
        *,
        region: str,
        audience: list[str],
        signing_algorithm: str = "ES384",
        duration_seconds: int = 300,
        header_name: str = "Authorization",
        scheme: str | None = "Bearer",
    ) -> None:
        if not audience:
            raise ValueError("agentcore-iam-jwt requires at least one audience")
        if signing_algorithm not in ("ES384", "RS256"):
            raise ValueError("signing_algorithm must be 'ES384' or 'RS256'")
        if not (60 <= duration_seconds <= 3600):
            raise ValueError("duration_seconds must be between 60 and 3600")
        self._region = region
        self._audience = audience
        self._signing_algorithm = signing_algorithm
        self._duration_seconds = duration_seconds
        self._header_name = header_name
        self._scheme = scheme

    def _fetch_sync(self) -> str:
        sts = boto3.client("sts", region_name=self._region)
        try:
            resp = sts.get_web_identity_token(
                Audience=self._audience,
                SigningAlgorithm=self._signing_algorithm,
                DurationSeconds=self._duration_seconds,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("FeatureDisabledException", "FeatureDisabled"):
                raise RuntimeError(
                    "AWS IAM Outbound Web Identity Federation is not enabled for this account. "
                    "Enable it (e.g. `agentcore identity setup-aws-jwt`) before using agentcore-iam-jwt."
                ) from exc
            raise
        return resp["WebIdentityToken"]

    async def fetch(self, *, force_refresh: bool = False) -> Credential:
        token = await asyncio.to_thread(self._fetch_sync)
        return Credential(
            value=token,
            header_name=self._header_name,
            scheme=self._scheme,
            expires_at=jwt_expiry(token),
        )
