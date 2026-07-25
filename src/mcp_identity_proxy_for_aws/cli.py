"""Command-line interface for mcp-identity-proxy-for-aws."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from mcp_identity_proxy_for_aws import __version__
from mcp_identity_proxy_for_aws.config import ProxyConfig
from mcp_identity_proxy_for_aws.proxy import run_proxy

AUTH_TYPES = [
    "agentcore-oauth",
    "agentcore-apikey",
    "agentcore-iam-jwt",
    "static-bearer",
    "static-apikey",
    "none",
]


def _parse_kv(pairs: list[str]) -> dict[str, str]:
    """Parse ``KEY=VALUE`` pairs (e.g. from repeated --header flags)."""
    result: dict[str, str] = {}
    for item in pairs:
        if "=" not in item:
            raise argparse.ArgumentTypeError(f"Expected KEY=VALUE, got: {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise argparse.ArgumentTypeError(f"Empty header name in: {item!r}")
        result[key] = value
    return result


def _merge_list(*sources: list[str] | None) -> list[str]:
    """Flatten singular (repeatable) and plural (multi-value/delimited) flags.

    Each source is a list of raw tokens; every token is further split on commas
    and whitespace so a single argument like ``"a b,c"`` yields ``["a", "b", "c"]``.
    Order is preserved and duplicates removed, so callers can freely combine the
    ``--scope`` (repeatable) and ``--scopes`` (array/delimited) styles.
    """
    seen: dict[str, None] = {}
    for source in sources:
        for token in source or []:
            for part in token.replace(",", " ").split():
                if part and part not in seen:
                    seen[part] = None
    return list(seen)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-identity-proxy-for-aws",
        description=(
            "Client-side MCP proxy that injects OAuth bearer tokens / API keys "
            "(via Amazon Bedrock AgentCore Identity or static credentials) into "
            "requests to an OAuth- or API-key-protected MCP server, and exposes "
            "it locally over stdio."
        ),
    )
    parser.add_argument("endpoint", help="Upstream MCP StreamableHTTP endpoint URL, e.g. https://<gateway>/mcp")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--name", default="mcp-identity-proxy-for-aws",
                        help="Proxy server name reported to MCP clients.")
    parser.add_argument(
        "--auth-type",
        choices=AUTH_TYPES,
        default=os.getenv("PROXY_AUTH_TYPE", "agentcore-oauth"),
        help="Authentication strategy (default: agentcore-oauth).",
    )

    # Generic credential placement
    parser.add_argument("--header", action="append", default=[], metavar="KEY=VALUE",
                        help="Extra static HTTP header to send upstream (repeatable).")
    parser.add_argument("--header-name", default=None,
                        help="Override the header the credential is injected into.")
    parser.add_argument("--scheme", default="AUTO",
                        help="Override the auth scheme prefix. Use 'none' for a raw header value.")
    parser.add_argument("--token-ttl", type=float, default=300.0,
                        help="Seconds to cache a credential without a known expiry (default: 300).")

    # AWS / AgentCore
    parser.add_argument("--region", default=None, help="AWS region (defaults to AWS_REGION / AWS_DEFAULT_REGION).")
    parser.add_argument("--provider-name", default=os.getenv("PROXY_PROVIDER_NAME"),
                        help="AgentCore Identity credential provider name (oauth/apikey auth types).")
    parser.add_argument("--scope", action="append", default=[], help="OAuth2 scope to request (repeatable).")
    parser.add_argument("--scopes", nargs="+", default=[], metavar="SCOPE",
                        help="OAuth2 scopes as one flag, e.g. --scopes aud-websearchgateway ticket:read "
                             "(also accepts a single comma/space-delimited string). Merged with --scope.")
    parser.add_argument("--oauth-flow", choices=["M2M", "USER_FEDERATION", "ON_BEHALF_OF_TOKEN_EXCHANGE"],
                        default="M2M", help="OAuth2 flow for agentcore-oauth (default: M2M).")
    parser.add_argument("--resource", action="append", default=[], help="OAuth2 resource indicator (repeatable).")
    parser.add_argument("--resources", nargs="+", default=[], metavar="RESOURCE",
                        help="OAuth2 resource indicators as one flag (merged with --resource).")
    parser.add_argument("--audience", action="append", default=[], help="OAuth2 audience (repeatable).")
    parser.add_argument("--audiences", nargs="+", default=[], metavar="AUDIENCE",
                        help="OAuth2 audiences as one flag (merged with --audience).")
    parser.add_argument("--callback-url", default=None, help="OAuth2 callback URL for 3LO (USER_FEDERATION) flows.")
    parser.add_argument("--workload-identity-name", default=os.getenv("PROXY_WORKLOAD_IDENTITY_NAME"),
                        help="AgentCore workload identity name (auto-created & cached if omitted).")
    parser.add_argument("--user-id", default=os.getenv("PROXY_USER_ID"),
                        help="Opaque end-user id for the workload access token (dev pattern).")
    parser.add_argument("--user-token", default=os.getenv("PROXY_USER_TOKEN"),
                        help="End-user JWT for the workload access token (production pattern).")

    # IAM JWT
    parser.add_argument("--iam-jwt-audience", action="append", default=[],
                        help="Audience for the AWS-signed JWT (agentcore-iam-jwt, repeatable).")
    parser.add_argument("--iam-jwt-signing-algorithm", choices=["ES384", "RS256"], default="ES384")
    parser.add_argument("--iam-jwt-duration-seconds", type=int, default=300)

    # Static
    parser.add_argument("--token", default=os.getenv("PROXY_STATIC_TOKEN"),
                        help="Static token / API key for static-* auth types (or PROXY_STATIC_TOKEN).")

    parser.add_argument("--log-level", default=os.getenv("PROXY_LOG_LEVEL", "INFO"),
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    return parser


def config_from_args(args: argparse.Namespace) -> ProxyConfig:
    return ProxyConfig(
        endpoint=args.endpoint,
        name=args.name,
        headers=_parse_kv(args.header),
        auth_type=args.auth_type,
        header_name=args.header_name,
        scheme=args.scheme,
        token_ttl=args.token_ttl,
        region=args.region,
        provider_name=args.provider_name,
        scopes=_merge_list(args.scope, args.scopes),
        oauth_flow=args.oauth_flow,
        resources=_merge_list(args.resource, args.resources),
        audiences=_merge_list(args.audience, args.audiences),
        callback_url=args.callback_url,
        workload_identity_name=args.workload_identity_name,
        user_id=args.user_id,
        user_token=args.user_token,
        iam_jwt_audience=args.iam_jwt_audience,
        iam_jwt_signing_algorithm=args.iam_jwt_signing_algorithm,
        iam_jwt_duration_seconds=args.iam_jwt_duration_seconds,
        static_token=args.token,
        log_level=args.log_level,
    )


def _configure_logging(level: str) -> None:
    # Always log to stderr: stdout is the MCP JSON-RPC channel and must stay clean.
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    config = config_from_args(args)
    try:
        run_proxy(config)
    except ValueError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:  # pragma: no cover - interactive interrupt
        return 130
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
