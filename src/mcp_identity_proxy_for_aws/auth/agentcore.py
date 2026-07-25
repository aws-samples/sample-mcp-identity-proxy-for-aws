"""Amazon Bedrock AgentCore Identity credential strategies.

These strategies use AgentCore Identity's token vault to obtain (and transparently
refresh) OAuth 2.0 access tokens and API keys, then inject them into outbound MCP
requests. They work *outside* AgentCore Runtime by bootstrapping a workload
identity locally and exchanging local AWS credentials (SigV4) for a workload
access token, exactly like the AgentCore SDK does for local development.

Flow (per credential fetch):

    local AWS creds (SigV4)
        -> GetWorkloadAccessToken(workloadName, userId|userToken)   [workload access token]
        -> GetResourceOauth2Token / GetResourceApiKey(providerName) [resource credential]
        -> inject as Authorization: Bearer <token>  or  <header>: <api key>

AgentCore Identity stores refresh tokens and auto-refreshes access tokens, so a
plain re-fetch normally returns a still-valid token; ``force_refresh`` maps to the
service's ``forceAuthentication`` to discard cached/revoked tokens.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from bedrock_agentcore.services.identity import IdentityClient

from mcp_identity_proxy_for_aws.auth.base import AuthStrategy
from mcp_identity_proxy_for_aws.credential import Credential, jwt_expiry

logger = logging.getLogger("mcp_identity_proxy_for_aws.auth.agentcore")

OAuthFlow = Literal["M2M", "USER_FEDERATION", "ON_BEHALF_OF_TOKEN_EXCHANGE"]

DEFAULT_CONFIG_DIR = Path.home() / ".mcp-identity-proxy-for-aws"
CONFIG_FILENAME = "workload.json"


class AgentCoreCredentialSource:
    """Shared AgentCore Identity access: workload identity + workload access token.

    A single instance is shared by the OAuth and API-key strategies so the
    workload identity is resolved once. All blocking boto3 calls are dispatched to
    a worker thread so the proxy's event loop is never blocked.
    """

    def __init__(
        self,
        *,
        region: str,
        workload_identity_name: str | None = None,
        user_id: str | None = None,
        user_token: str | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self._region = region
        self._user_token = user_token
        self._explicit_workload_name = workload_identity_name
        self._explicit_user_id = user_id
        self._config_dir = config_dir or DEFAULT_CONFIG_DIR
        self._client = IdentityClient(region)
        self._resolved_name: str | None = None
        self._resolved_user_id: str | None = None
        self._lock = threading.Lock()

    @property
    def client(self) -> IdentityClient:
        return self._client

    @property
    def config_path(self) -> Path:
        return self._config_dir / CONFIG_FILENAME

    # -- config persistence -------------------------------------------------
    def _load_config(self) -> dict[str, Any]:
        path = self.config_path
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read workload config at %s; ignoring.", path)
            return {}

    def _save_config(self, config: dict[str, Any]) -> None:
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("Could not persist workload config to %s.", self.config_path)

    def _resolve_identity(self) -> tuple[str, str | None]:
        """Resolve (workload_identity_name, user_id), creating/persisting as needed.

        Runs synchronously; callers dispatch it via ``asyncio.to_thread``. The
        ``threading.Lock`` guards the create-if-missing logic against concurrent
        credential fetches from multiple in-flight requests.
        """
        with self._lock:
            if self._resolved_name is not None:
                return self._resolved_name, self._resolved_user_id

            config = self._load_config()
            name = self._explicit_workload_name or config.get("workload_identity_name")
            if not name:
                name = self._client.create_workload_identity()["name"]
                logger.info("Created AgentCore workload identity: %s", name)

            user_id: str | None = None
            if not self._user_token:
                user_id = self._explicit_user_id or config.get("user_id")
                if not user_id:
                    user_id = uuid.uuid4().hex[:8]
                    logger.info("Generated local user id: %s", user_id)

            new_config = {"workload_identity_name": name}
            if user_id:
                new_config["user_id"] = user_id
            if new_config != config:
                self._save_config(new_config)

            self._resolved_name = name
            self._resolved_user_id = user_id
            return name, user_id

    def _fetch_workload_token(self) -> str:
        name, user_id = self._resolve_identity()
        if self._user_token:
            resp = self._client.get_workload_access_token(name, user_token=self._user_token)
        elif user_id:
            resp = self._client.get_workload_access_token(name, user_id=user_id)
        else:
            resp = self._client.get_workload_access_token(name)
        return resp["workloadAccessToken"]

    def workload_token_sync(self) -> str:
        """Obtain a workload access token synchronously (for use inside a worker thread)."""
        return self._fetch_workload_token()

    async def workload_token(self) -> str:
        """Obtain a workload access token (off the event loop)."""
        return await asyncio.to_thread(self._fetch_workload_token)


def _default_on_auth_url(url: str) -> None:
    # For 3LO flows: surface the authorization URL to the operator via stderr so it
    # never contaminates the stdio JSON-RPC channel on stdout.
    print(
        "\n[mcp-identity-proxy-for-aws] Authorization required. Open this URL to continue:\n"
        f"{url}\n",
        file=sys.stderr,
        flush=True,
    )


class AgentCoreOAuthStrategy(AuthStrategy):
    """Obtain an OAuth 2.0 access token from an AgentCore Identity OAuth2 provider."""

    name = "agentcore-oauth"

    def __init__(
        self,
        *,
        source: AgentCoreCredentialSource,
        provider_name: str,
        scopes: list[str] | None = None,
        auth_flow: OAuthFlow = "M2M",
        resources: list[str] | None = None,
        audiences: list[str] | None = None,
        callback_url: str | None = None,
        custom_parameters: dict[str, str] | None = None,
        header_name: str = "Authorization",
        scheme: str | None = "Bearer",
        on_auth_url: Callable[[str], Any] | None = None,
    ) -> None:
        if not provider_name:
            raise ValueError("agentcore-oauth requires a provider_name")
        self._source = source
        self._provider_name = provider_name
        self._scopes = scopes or []
        self._auth_flow: OAuthFlow = auth_flow
        self._resources = resources
        self._audiences = audiences
        self._callback_url = callback_url
        self._custom_parameters = custom_parameters
        self._header_name = header_name
        self._scheme = scheme
        self._on_auth_url = on_auth_url or _default_on_auth_url

    def _fetch_sync(self, force_refresh: bool) -> str:
        """Blocking credential fetch, run in a worker thread by :meth:`fetch`.

        The AgentCore SDK's ``get_token`` is a coroutine that performs blocking
        boto3 calls (and, for 3LO, polls). Running it under ``asyncio.run`` inside
        a worker thread keeps the proxy's main event loop responsive even during a
        long ``USER_FEDERATION`` authorization flow.
        """
        workload_token = self._source.workload_token_sync()
        return asyncio.run(
            self._source.client.get_token(
                provider_name=self._provider_name,
                agent_identity_token=workload_token,
                scopes=self._scopes,
                resources=self._resources,
                audiences=self._audiences,
                auth_flow=self._auth_flow,
                callback_url=self._callback_url,
                custom_parameters=self._custom_parameters,
                on_auth_url=self._on_auth_url,
                force_authentication=force_refresh,
            )
        )

    async def fetch(self, *, force_refresh: bool = False) -> Credential:
        token = await asyncio.to_thread(self._fetch_sync, force_refresh)
        return Credential(
            value=token,
            header_name=self._header_name,
            scheme=self._scheme,
            expires_at=jwt_expiry(token),
        )


class AgentCoreApiKeyStrategy(AuthStrategy):
    """Obtain an API key from an AgentCore Identity API-key credential provider."""

    name = "agentcore-apikey"

    def __init__(
        self,
        *,
        source: AgentCoreCredentialSource,
        provider_name: str,
        header_name: str = "x-api-key",
        scheme: str | None = None,
    ) -> None:
        if not provider_name:
            raise ValueError("agentcore-apikey requires a provider_name")
        self._source = source
        self._provider_name = provider_name
        self._header_name = header_name
        self._scheme = scheme

    def _fetch_sync(self, force_refresh: bool) -> str:
        workload_token = self._source.workload_token_sync()
        return asyncio.run(
            self._source.client.get_api_key(
                provider_name=self._provider_name,
                agent_identity_token=workload_token,
            )
        )

    async def fetch(self, *, force_refresh: bool = False) -> Credential:
        api_key = await asyncio.to_thread(self._fetch_sync, force_refresh)
        return Credential(
            value=api_key,
            header_name=self._header_name,
            scheme=self._scheme,
            expires_at=None,
        )


def resolve_region(explicit: str | None) -> str:
    """Resolve the AWS region from an explicit value, env vars, or a sane default."""
    if explicit:
        return explicit
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
