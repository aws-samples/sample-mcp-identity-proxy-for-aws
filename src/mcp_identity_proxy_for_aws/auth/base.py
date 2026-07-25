"""Base class for pluggable authentication strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

from mcp_identity_proxy_for_aws.credential import Credential


class AuthStrategy(ABC):
    """A strategy that produces a :class:`Credential` for outbound MCP requests.

    Implementations obtain a bearer token / API key from some source (Amazon
    Bedrock AgentCore Identity, AWS STS, or a static value) and return it wrapped
    in a :class:`Credential`. Caching, TTL handling and 401-triggered refresh are
    handled by :class:`mcp_identity_proxy_for_aws.httpx_auth.InjectingAuth`; a strategy
    only needs to know how to fetch a fresh credential.
    """

    #: Human-friendly identifier used in logs and errors.
    name: str = "auth"

    @abstractmethod
    async def fetch(self, *, force_refresh: bool = False) -> Credential:
        """Fetch a (fresh) credential.

        Args:
            force_refresh: When True, bypass any provider-side cache and force a
                brand new credential. This is invoked after the upstream returns
                ``401 Unauthorized`` so a stale/revoked token can be replaced.
        """
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release any resources held by the strategy. Optional override."""
        return None
