# mcp-identity-proxy-for-aws

**English** | [中文](README.zh-CN.md)

A lightweight, **client-side** MCP proxy that injects **OAuth 2.0 bearer tokens**
or **API keys** into requests to MCP servers that authenticate with OAuth or API
keys — for example [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
targets that use **JWT / OAuth inbound authorization**, or any third‑party MCP
server behind a bearer token or `x-api-key`.

It obtains and transparently refreshes those credentials using
[**Amazon Bedrock AgentCore Identity**](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html)
(token vault + credential providers), or from static values you supply.

Think of it as the **bearer/OAuth counterpart to
[`mcp-proxy-for-aws`](https://github.com/aws/mcp-proxy-for-aws)**: where that tool
SigV4‑signs each request for IAM‑protected MCP servers, this tool attaches
`Authorization: Bearer <token>` (or an API‑key header) for OAuth/API‑key‑protected
MCP servers.

---

## Why this exists

| | `mcp-proxy-for-aws` | **`mcp-identity-proxy-for-aws`** (this project) |
|---|---|---|
| Outbound auth | **SigV4** signs every request with local AWS creds | Injects **`Authorization: Bearer …`** or an **API‑key header** |
| Target inbound auth | AgentCore Gateway/Runtime with **IAM (SigV4)** | AgentCore Gateway with **JWT/OAuth** inbound, or any OAuth / API‑key MCP server |
| Token source | n/a (just signs) | **AgentCore Identity** vault (auto‑refresh), AWS STS JWT, or static |

Many MCP clients (Claude Desktop, Kiro CLI, etc.) can't natively fetch/refresh an
OAuth token from AgentCore Identity or an enterprise IdP. This proxy runs locally
over **stdio**, forwards the full MCP protocol to the remote **StreamableHTTP**
server, and handles the credential lifecycle for you.

## Architecture

```
┌──────────────┐   stdio     ┌──────────────────────────────────┐   HTTPS         ┌──────────────────────┐
│  MCP Client  │──JSON‑RPC──▶│    mcp-identity-proxy-for-aws    │────────────────▶│  Remote MCP Server   │
│ (Kiro/Claude)│◀────────────│   stdio  ⇄  StreamableHTTP       │  Authorization: │  AgentCore Gateway   │
└──────────────┘             │                                  │   Bearer <tok>  │  / OAuth MCP server  │
                             │  ┌────────────────────────────┐  │   or x-api-key  │  / API‑key MCP server│
                             │  │ InjectingAuth (httpx.Auth) │  │                 └──────────────────────┘
                             │  │  cache + TTL + 401 retry   │  │
                             │  └─────────────┬──────────────┘  │
                             │        AuthStrategy (pluggable)  │
                             └──────────────────┼───────────────┘
                                                │ boto3 SigV4 (local AWS creds)
                                                ▼
                                 ┌──────────────────────────────────┐
                                 │       AgentCore Identity         │
                                 │  GetWorkloadAccessToken(...)     │  ← bootstrap workload identity
                                 │  GetResourceOauth2Token (M2M/3LO)│  ← token vault + auto refresh
                                 │  GetResourceApiKey(...)          │
                                 └──────────────────────────────────┘
```

Per credential fetch (AgentCore path):

1. Use **local AWS credentials (SigV4)** to call `GetWorkloadAccessToken`
   (bootstrapping a workload identity + user id, cached in
   `~/.mcp-identity-proxy-for-aws/workload.json`).
2. Exchange the workload access token for the resource credential via
   `GetResourceOauth2Token` (OAuth, `M2M` or `USER_FEDERATION`) or
   `GetResourceApiKey`.
3. Inject it into the outbound MCP HTTP request.
4. Cache it (JWT `exp` if present, otherwise a configurable TTL), and on a `401`
   force a refresh (`forceAuthentication=true`) and retry once.

> **Important:** the proxy still needs **local AWS credentials** — but only to talk
> to AgentCore Identity to *fetch* the bearer token / API key. What is sent to the
> MCP server is the OAuth token / API key, **not** a SigV4 signature. For an
> AgentCore Gateway using JWT inbound auth, the AgentCore Identity OAuth2 credential
> provider must point at the **same IdP the gateway trusts**, with matching
> audience/scopes.

## Feasibility (verified)

- The proxy transport reuses the same proven pattern as `mcp-proxy-for-aws`: a
  stdio ⇄ StreamableHTTP bridge where outbound auth is applied via an
  `httpx.Auth`. Swapping SigV4 signing for a header injection is a drop‑in change.
- AgentCore Identity works **outside** AgentCore Runtime: the AgentCore SDK's
  local bootstrap (`_set_up_local_auth`) creates a workload identity and exchanges
  local IAM creds for a workload access token, then calls
  `GetResourceOauth2Token` / `GetResourceApiKey`. This proxy uses the same
  `bedrock_agentcore.services.identity.IdentityClient`.
- Token request/refresh is managed by the AgentCore token vault (refresh tokens
  stored automatically; `forceAuthentication` discards revoked tokens).

## Install

Requires Python 3.10+ and (for the AgentCore strategies) AWS credentials.

```bash
# From source with uv
git clone <your-fork-url> mcp-identity-proxy-for-aws
cd sample-mcp-identity-proxy-for-aws
uv sync

# Run it
uv run mcp-identity-proxy-for-aws --help
```

Once published to PyPI you'd run it via `uvx mcp-identity-proxy-for-aws@<version> <endpoint> ...`.

## Auth strategies

Select with `--auth-type`:

| `--auth-type` | Credential source | Injected as (default) |
|---|---|---|
| `agentcore-oauth` | AgentCore Identity OAuth2 provider (`M2M` 2LO / `USER_FEDERATION` 3LO) | `Authorization: Bearer <token>` |
| `agentcore-apikey` | AgentCore Identity API‑key provider | `x-api-key: <key>` |
| `agentcore-iam-jwt` | AWS STS `GetWebIdentityToken` (AWS‑signed JWT, no client secret) | `Authorization: Bearer <jwt>` |
| `static-bearer` | `--token` / `PROXY_STATIC_TOKEN` | `Authorization: Bearer <token>` |
| `static-apikey` | `--token` / `PROXY_STATIC_TOKEN` | `x-api-key: <key>` |
| `none` | (no auth) | — |

Header placement is configurable for every strategy:

- `--header-name Authorization` — which header to inject into.
- `--scheme Bearer` — the prefix; use `--scheme none` for a raw value (e.g. `x-api-key`).
- `--header KEY=VALUE` — extra static headers to always send (repeatable).

## Usage examples

### AgentCore Gateway (OAuth / JWT inbound, machine‑to‑machine)

```bash
uv run mcp-identity-proxy-for-aws \
  https://my-gateway-id.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp \
  --auth-type agentcore-oauth \
  --provider-name my-gateway-oauth-provider \
  --oauth-flow M2M \
  --scopes gateway/invoke resource/read \
  --region us-east-1
```

`my-gateway-oauth-provider` is an AgentCore Identity **OAuth2 credential provider**
configured for the IdP (e.g. a Cognito user pool + resource server, or Keycloak)
that the gateway's inbound authorizer trusts. The `M2M` (client‑credentials) flow
needs no user interaction.

`--scopes` takes multiple scopes in one flag (`--scopes a b`, or a single
delimited string `--scopes "a,b"`); the repeatable `--scope a --scope b` form also
works and both are merged.

> **Verified real example (Keycloak inbound + per‑tool scope interceptor).** The
> gateway's inbound authorizer required `aud=websearchgateway`, and a request
> interceptor enforced per‑tool scopes (`ticket:read`, `ticket:write`). With
> Keycloak, the audience is delivered by a **client scope** (`aud-websearchgateway`),
> not a separate parameter — so it is requested as a scope alongside the tool
> scope. This exact command returned live tool results through the proxy:
>
> ```bash
> uv run mcp-identity-proxy-for-aws \
>   https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp \
>   --auth-type agentcore-oauth \
>   --provider-name <keycloak-m2m-provider> \
>   --oauth-flow M2M \
>   --scopes aud-websearchgateway ticket:read \
>   --region us-east-1
> ```
>
> Takeaways: **you don't pass an "audience" flag** — ensure the IdP client emits the
> gateway's expected `aud` (via a default or optional client scope). **You do pass
> `--scopes`** when the gateway/resource requires specific scopes that are optional
> (request‑time) client scopes on your IdP client.

### AgentCore Gateway (3‑legged OAuth, on behalf of a user)

```bash
uv run mcp-identity-proxy-for-aws https://…/mcp \
  --auth-type agentcore-oauth \
  --provider-name my-google-provider \
  --oauth-flow USER_FEDERATION \
  --scope "https://www.googleapis.com/auth/drive.readonly" \
  --callback-url https://your-registered-callback
```

On first use the proxy prints an authorization URL to **stderr**; open it, and the
token (plus refresh token) is stored in the AgentCore vault for subsequent runs.

### API‑key‑protected MCP server (key stored in AgentCore Identity)

Store the key once in an AgentCore Identity **API‑key credential provider**:

```bash
aws bedrock-agentcore-control create-api-key-credential-provider \
  --name my-service-apikey --api-key "$YOUR_API_KEY" --region us-east-1
```

Then have the proxy fetch it from the vault and inject it as `x-api-key`:

```bash
uv run mcp-identity-proxy-for-aws https://api.example.com/mcp \
  --auth-type agentcore-apikey \
  --provider-name my-service-apikey \
  --header-name x-api-key \
  --region us-east-1
```

By default the key is injected verbatim into `--header-name` (no scheme). To send
it as a bearer token instead, use `--header-name Authorization --scheme Bearer`.

> **Verified:** this path was tested end‑to‑end — the proxy retrieved the key from
> the AgentCore Identity token vault and injected it as `x-api-key`, and the
> upstream MCP server accepted the request. The retrieval uses the same local
> workload‑identity bootstrap as the OAuth strategies (local AWS credentials →
> `GetResourceApiKey`).

### AWS‑signed JWT (gateway trusts your AWS account as an OIDC issuer)

```bash
uv run mcp-identity-proxy-for-aws https://…/mcp \
  --auth-type agentcore-iam-jwt \
  --iam-jwt-audience https://my-gateway-audience \
  --region us-east-1
```

### Static bearer / API key (local dev, simple servers)

```bash
uv run mcp-identity-proxy-for-aws https://api.example.com/mcp \
  --auth-type static-bearer --token "$MY_TOKEN"

uv run mcp-identity-proxy-for-aws https://api.example.com/mcp \
  --auth-type static-apikey --token "$MY_KEY" --header-name x-api-key
```

## MCP client configuration (Kiro CLI / Claude Desktop)

Add to your MCP client config (e.g. `~/.kiro/settings/mcp.json`):

```json
{
  "mcpServers": {
    "my-gateway": {
      "command": "uvx",
      "args": [
        "mcp-identity-proxy-for-aws@latest",
        "https://my-gateway-id.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
        "--auth-type", "agentcore-oauth",
        "--provider-name", "my-gateway-oauth-provider",
        "--oauth-flow", "M2M",
        "--region", "us-east-1"
      ],
      "env": {
        "AWS_PROFILE": "my-profile",
        "AWS_REGION": "us-east-1"
      }
    }
  }
}
```

Running from a local checkout instead of PyPI:

```json
{
  "mcpServers": {
    "my-gateway": {
      "command": "uv",
      "args": [
        "--directory", "/path/to/mcp-identity-proxy-for-aws",
        "run", "mcp-identity-proxy-for-aws",
        "https://…/mcp",
        "--auth-type", "agentcore-oauth",
        "--provider-name", "my-gateway-oauth-provider",
        "--oauth-flow", "M2M",
        "--region", "us-east-1",
        "--scopes", "aud-websearchgateway", "ticket:read"
      ]
    }
  }
}
```

## CLI reference

Run `mcp-identity-proxy-for-aws --help`. Key options:

| Option | Description | Default |
|---|---|---|
| `endpoint` (positional) | Upstream MCP StreamableHTTP URL | required |
| `--auth-type` | One of the strategies above | `agentcore-oauth` |
| `--provider-name` | AgentCore Identity credential provider (oauth/apikey) | `PROXY_PROVIDER_NAME` |
| `--oauth-flow` | `M2M` / `USER_FEDERATION` / `ON_BEHALF_OF_TOKEN_EXCHANGE` | `M2M` |
| `--scope` | OAuth scope (repeatable) | — |
| `--scopes` | OAuth scopes in one flag: `--scopes a b` or `--scopes "a,b"` (merged with `--scope`) | — |
| `--resource` / `--resources` | OAuth resource indicators (repeatable / one-flag array) | — |
| `--audience` / `--audiences` | OAuth audiences (repeatable / one-flag array) | — |
| `--callback-url` | 3LO callback URL | — |
| `--region` | AWS region | `AWS_REGION` / `AWS_DEFAULT_REGION` / `us-east-1` |
| `--workload-identity-name` | Reuse a specific workload identity | auto‑created + cached |
| `--user-id` / `--user-token` | End‑user id (dev) / JWT (prod) for the workload token | generated |
| `--iam-jwt-audience` | Audience(s) for `agentcore-iam-jwt` (repeatable) | — |
| `--token` | Static token/key for `static-*` | `PROXY_STATIC_TOKEN` |
| `--header-name` / `--scheme` | Credential header + scheme (`none` = raw) | per strategy |
| `--header KEY=VALUE` | Extra static header (repeatable) | — |
| `--token-ttl` | Cache seconds for credentials without a known expiry | `300` |
| `--log-level` | `DEBUG`…`CRITICAL` (logs go to **stderr**) | `INFO` |

## AWS permissions

The identity used by the proxy (local AWS creds) needs, for the AgentCore
strategies:

```
bedrock-agentcore:GetWorkloadAccessToken
bedrock-agentcore:GetWorkloadAccessTokenForUserId
bedrock-agentcore:GetWorkloadAccessTokenForJWT
bedrock-agentcore:GetResourceOauth2Token      # agentcore-oauth
bedrock-agentcore:GetResourceApiKey           # agentcore-apikey
bedrock-agentcore-control:CreateWorkloadIdentity   # first-run bootstrap
```

For `agentcore-iam-jwt`: `sts:GetWebIdentityToken` (and AWS IAM Outbound Web
Identity Federation enabled for the account).

## How it works internally

- **`auth/`** — pluggable `AuthStrategy` implementations returning a `Credential`
  (value + header + scheme + optional expiry). `factory.build_strategy` maps CLI
  config to a strategy.
- **`httpx_auth.InjectingAuth`** — an `httpx.Auth` that caches the credential,
  refreshes it on JWT `exp` / TTL, injects the header, and on a `401` forces a
  refresh and retries the request once.
- **`proxy.py`** — builds a [FastMCP](https://github.com/jlowin/fastmcp) proxy
  (`create_proxy` + `ProxyClient` + `StreamableHttpTransport(auth=…)`) so the full
  MCP surface (tools, resources, prompts) is transparently forwarded, and serves
  it over stdio. `stdout` is reserved for JSON‑RPC; all logs go to `stderr`.

## Development

```bash
uv sync                 # install with dev extras
uv run pytest -q        # 52 tests incl. live stdio<->HTTP e2e for bearer + api-key
uv run ruff check .     # lint
uv run pyright          # type check
```

The end‑to‑end tests (`tests/test_e2e_bearer.py`, `tests/test_e2e_apikey.py`) start
a real bearer‑ / api‑key‑protected StreamableHTTP MCP server and drive the proxy as
a subprocess over stdio, verifying both successful and rejected (wrong‑credential)
calls.

## Security notes

- The proxy exposes the upstream MCP server locally; treat it as you would the
  underlying tools. Credentials are held in memory only (plus the AgentCore vault
  server‑side). `~/.mcp-identity-proxy-for-aws/workload.json` stores only the workload
  identity name and a random user id — **no secrets**.
- **Use HTTPS endpoints.** The proxy injects the credential into every request; if
  the endpoint is not `https://` (and not localhost), the credential is sent in
  cleartext. The proxy logs a warning in this case.
- **Prefer environment variables over `--token` / `--header` for secrets.** Values
  passed as CLI arguments are visible in the process table (`ps`) and shell
  history; set `PROXY_STATIC_TOKEN` instead.
- **Region:** if `--region` / `AWS_REGION` / `AWS_DEFAULT_REGION` are all unset,
  the proxy defaults to `us-east-1`. Set it explicitly to avoid confusing
  cross-region `AccessDenied` / `ResourceNotFound` errors.
- **`ON_BEHALF_OF_TOKEN_EXCHANGE`** is accepted by `--oauth-flow` but is currently
  experimental / unverified; `M2M` and `USER_FEDERATION` are the tested flows.
- Prefer least‑privilege IAM policies scoped to the specific credential providers.
- This is not an official AWS project. Test thoroughly before production use.

## License

Apache-2.0
