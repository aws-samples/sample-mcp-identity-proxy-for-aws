# mcp-identity-proxy-for-aws

[English](README.md) | **中文**

一个轻量的**客户端** MCP 代理，为使用 OAuth 或 API key 鉴权的 MCP server
注入 **OAuth 2.0 bearer token** 或 **API key** —— 例如使用 **JWT / OAuth 入站授权**的
[Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
目标，或任何需要 bearer token / `x-api-key` 的第三方 MCP server。

它通过 [**Amazon Bedrock AgentCore Identity**](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html)
（token vault + credential providers）获取并透明刷新这些凭证，也支持你直接提供的静态凭证。

可以把它理解为 [`mcp-proxy-for-aws`](https://github.com/aws/mcp-proxy-for-aws)
的 **bearer/OAuth 版本**：后者为 IAM 保护的 MCP server 对每个请求做 SigV4 签名，
而本项目为 OAuth / API-key 保护的 MCP server 附加
`Authorization: Bearer <token>`（或一个 API-key header）。

---

## 为什么需要它

| | `mcp-proxy-for-aws` | **`mcp-identity-proxy-for-aws`**（本项目） |
|---|---|---|
| 出站鉴权 | 用本地 AWS 凭证对每个请求做 **SigV4** 签名 | 注入 **`Authorization: Bearer …`** 或一个 **API-key header** |
| 目标入站鉴权 | 使用 **IAM (SigV4)** 的 AgentCore Gateway/Runtime | 使用 **JWT/OAuth** 入站的 AgentCore Gateway，或任意 OAuth / API-key MCP server |
| Token 来源 | 无（仅签名） | **AgentCore Identity** vault（自动刷新）、AWS STS JWT，或静态值 |

很多 MCP 客户端（Claude Desktop、Kiro CLI 等）无法原生地从 AgentCore Identity
或企业 IdP 获取/刷新 OAuth token。本代理在本地通过 **stdio** 运行，把完整的 MCP
协议转发到远端的 **StreamableHTTP** server，并为你处理凭证的整个生命周期。

## 架构

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
                                 │  GetWorkloadAccessToken(...)     │  ← 引导 workload identity
                                 │  GetResourceOauth2Token (M2M/3LO)│  ← token vault + 自动刷新
                                 │  GetResourceApiKey(...)          │
                                 └──────────────────────────────────┘
```

每次获取凭证（AgentCore 路径）：

1. 用**本地 AWS 凭证（SigV4）**调用 `GetWorkloadAccessToken`
   （引导出一个 workload identity + user id，缓存在
   `~/.mcp-identity-proxy-for-aws/workload.json`）。
2. 用 workload access token 换取资源凭证：`GetResourceOauth2Token`
   （OAuth，`M2M` 或 `USER_FEDERATION`）或 `GetResourceApiKey`。
3. 将其注入到出站的 MCP HTTP 请求中。
4. 缓存（若是 JWT 则用 `exp`，否则用可配置的 TTL）；遇到 `401` 时
   强制刷新（`forceAuthentication=true`）并重试一次。

> **重要：** 代理仍然需要**本地 AWS 凭证** —— 但仅用于调用 AgentCore Identity 去
> *获取* bearer token / API key。发送给 MCP server 的是 OAuth token / API key，
> **不是** SigV4 签名。对于使用 JWT 入站鉴权的 AgentCore Gateway，AgentCore Identity
> 的 OAuth2 credential provider 必须指向 **gateway 所信任的同一个 IdP**，且
> audience/scope 对齐。

## 可行性（已验证）

- 代理的传输层复用了与 `mcp-proxy-for-aws` 相同、已被验证的模式：一个
  stdio ⇄ StreamableHTTP 桥，出站鉴权通过 `httpx.Auth` 施加。把 SigV4 签名替换为
  header 注入是一个即插即用的改动。
- AgentCore Identity 可以在 AgentCore Runtime **之外**工作：AgentCore SDK 的本地
  引导逻辑（`_set_up_local_auth`）会创建 workload identity、用本地 IAM 凭证换取
  workload access token，再调用 `GetResourceOauth2Token` / `GetResourceApiKey`。
  本代理使用同一个 `bedrock_agentcore.services.identity.IdentityClient`。
- Token 的请求/刷新由 AgentCore token vault 管理（refresh token 自动存储；
  `forceAuthentication` 丢弃已撤销的 token）。

## 安装

需要 Python 3.10+，以及（对于 AgentCore 策略）AWS 凭证。

```bash
# 从源码用 uv 安装
git clone <your-fork-url> mcp-identity-proxy-for-aws
cd mcp-identity-proxy-for-aws
uv sync

# 运行
uv run mcp-identity-proxy-for-aws --help
```

发布到 PyPI 后，可用 `uvx mcp-identity-proxy-for-aws@<version> <endpoint> ...` 运行。

## 鉴权策略

用 `--auth-type` 选择：

| `--auth-type` | 凭证来源 | 注入方式（默认） |
|---|---|---|
| `agentcore-oauth` | AgentCore Identity OAuth2 provider（`M2M` 2LO / `USER_FEDERATION` 3LO） | `Authorization: Bearer <token>` |
| `agentcore-apikey` | AgentCore Identity API-key provider | `x-api-key: <key>` |
| `agentcore-iam-jwt` | AWS STS `GetWebIdentityToken`（AWS 签名的 JWT，无需 client secret） | `Authorization: Bearer <jwt>` |
| `static-bearer` | `--token` / `PROXY_STATIC_TOKEN` | `Authorization: Bearer <token>` |
| `static-apikey` | `--token` / `PROXY_STATIC_TOKEN` | `x-api-key: <key>` |
| `none` | （不鉴权） | — |

每种策略的 header 位置都可配置：

- `--header-name Authorization` —— 注入到哪个 header。
- `--scheme Bearer` —— 前缀；用 `--scheme none` 表示原始值（例如 `x-api-key`）。
- `--header KEY=VALUE` —— 始终附带的额外静态 header（可重复）。

## 使用示例

### AgentCore Gateway（OAuth / JWT 入站，机器对机器）

```bash
uv run mcp-identity-proxy-for-aws \
  https://my-gateway-id.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp \
  --auth-type agentcore-oauth \
  --provider-name my-gateway-oauth-provider \
  --oauth-flow M2M \
  --scopes gateway/invoke resource/read \
  --region us-east-1
```

`my-gateway-oauth-provider` 是一个 AgentCore Identity **OAuth2 credential provider**，
配置为 gateway 入站授权器所信任的 IdP（例如 Cognito user pool + resource server，
或 Keycloak）。`M2M`（client-credentials）流程无需用户交互。

`--scopes` 可以在一个参数里给多个 scope（`--scopes a b`，或单个分隔字符串
`--scopes "a,b"`）；可重复的 `--scope a --scope b` 形式同样支持，两者会合并。

> **已验证的真实示例（Keycloak 入站 + 按工具 scope 拦截器）。** 该 gateway 的入站
> 授权器要求 `aud=websearchgateway`，并且有一个请求拦截器强制按工具校验 scope
> （`ticket:read`、`ticket:write`）。在 Keycloak 中，audience 是由一个 **client scope**
> （`aud-websearchgateway`）注入的，而不是一个单独的参数 —— 因此它作为一个 scope
> 与工具 scope 一起被请求。下面这条命令通过代理返回了真实的工具结果：
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
> 要点：**你不需要传一个 "audience" 参数** —— 只要确保 IdP client 能输出 gateway
> 期望的 `aud`（通过默认或可选的 client scope）。**当 gateway/资源要求的 scope 是
> IdP client 上的可选（请求时）client scope 时，你需要传 `--scopes`。**

### AgentCore Gateway（三方 OAuth，代表用户）

```bash
uv run mcp-identity-proxy-for-aws https://…/mcp \
  --auth-type agentcore-oauth \
  --provider-name my-google-provider \
  --oauth-flow USER_FEDERATION \
  --scope "https://www.googleapis.com/auth/drive.readonly" \
  --callback-url https://your-registered-callback
```

首次使用时，代理会把授权 URL 打印到 **stderr**；打开它完成授权后，token（连同
refresh token）会存入 AgentCore vault 供后续使用。

### API-key 保护的 MCP server（key 存于 AgentCore Identity）

先把 key 存入一个 AgentCore Identity **API-key credential provider**：

```bash
aws bedrock-agentcore-control create-api-key-credential-provider \
  --name my-service-apikey --api-key "$YOUR_API_KEY" --region us-east-1
```

然后让代理从 vault 取出并注入为 `x-api-key`：

```bash
uv run mcp-identity-proxy-for-aws https://api.example.com/mcp \
  --auth-type agentcore-apikey \
  --provider-name my-service-apikey \
  --header-name x-api-key \
  --region us-east-1
```

默认情况下 key 会原样注入到 `--header-name`（无前缀）。若要作为 bearer token 发送，
使用 `--header-name Authorization --scheme Bearer`。

> **已验证：** 该路径已做端到端测试 —— 代理从 AgentCore Identity token vault 取出
> key、注入为 `x-api-key`，上游 MCP server 接受了该请求。其获取过程与 OAuth 策略
> 使用相同的本地 workload-identity 引导（本地 AWS 凭证 → `GetResourceApiKey`）。

### AWS 签名的 JWT（gateway 信任你的 AWS 账号作为 OIDC issuer）

```bash
uv run mcp-identity-proxy-for-aws https://…/mcp \
  --auth-type agentcore-iam-jwt \
  --iam-jwt-audience https://my-gateway-audience \
  --region us-east-1
```

### 静态 bearer / API key（本地开发、简单 server）

```bash
uv run mcp-identity-proxy-for-aws https://api.example.com/mcp \
  --auth-type static-bearer --token "$MY_TOKEN"

uv run mcp-identity-proxy-for-aws https://api.example.com/mcp \
  --auth-type static-apikey --token "$MY_KEY" --header-name x-api-key
```

## MCP 客户端配置（Kiro CLI / Claude Desktop）

添加到你的 MCP 客户端配置（例如 `~/.kiro/settings/mcp.json`）：

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

从本地检出（而非 PyPI）运行：

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

## CLI 参考

运行 `mcp-identity-proxy-for-aws --help`。主要选项：

| 选项 | 说明 | 默认值 |
|---|---|---|
| `endpoint`（位置参数） | 上游 MCP StreamableHTTP URL | 必填 |
| `--auth-type` | 上面某种策略 | `agentcore-oauth` |
| `--provider-name` | AgentCore Identity credential provider（oauth/apikey） | `PROXY_PROVIDER_NAME` |
| `--oauth-flow` | `M2M` / `USER_FEDERATION` / `ON_BEHALF_OF_TOKEN_EXCHANGE` | `M2M` |
| `--scope` | OAuth scope（可重复） | — |
| `--scopes` | 一个参数给多个 scope：`--scopes a b` 或 `--scopes "a,b"`（与 `--scope` 合并） | — |
| `--resource` / `--resources` | OAuth resource indicators（可重复 / 单参数数组） | — |
| `--audience` / `--audiences` | OAuth audiences（可重复 / 单参数数组） | — |
| `--callback-url` | 3LO 回调 URL | — |
| `--region` | AWS region | `AWS_REGION` / `AWS_DEFAULT_REGION` / `us-east-1` |
| `--workload-identity-name` | 复用指定的 workload identity | 自动创建并缓存 |
| `--user-id` / `--user-token` | workload token 的终端用户 id（开发）/ JWT（生产） | 自动生成 |
| `--iam-jwt-audience` | `agentcore-iam-jwt` 的 audience（可重复） | — |
| `--token` | `static-*` 的静态 token/key | `PROXY_STATIC_TOKEN` |
| `--header-name` / `--scheme` | 凭证 header 与前缀（`none` = 原始值） | 按策略而定 |
| `--header KEY=VALUE` | 额外静态 header（可重复） | — |
| `--token-ttl` | 无已知过期时间的凭证的缓存秒数 | `300` |
| `--log-level` | `DEBUG`…`CRITICAL`（日志走 **stderr**） | `INFO` |

## AWS 权限

代理使用的身份（本地 AWS 凭证）在 AgentCore 策略下需要：

```
bedrock-agentcore:GetWorkloadAccessToken
bedrock-agentcore:GetWorkloadAccessTokenForUserId
bedrock-agentcore:GetWorkloadAccessTokenForJWT
bedrock-agentcore:GetResourceOauth2Token      # agentcore-oauth
bedrock-agentcore:GetResourceApiKey           # agentcore-apikey
bedrock-agentcore-control:CreateWorkloadIdentity   # 首次运行引导
```

对于 `agentcore-iam-jwt`：需要 `sts:GetWebIdentityToken`（并且账号已启用 AWS IAM
Outbound Web Identity Federation）。

## 内部工作原理

- **`auth/`** —— 可插拔的 `AuthStrategy` 实现，返回一个 `Credential`
  （值 + header + 前缀 + 可选过期时间）。`factory.build_strategy` 把 CLI 配置映射为
  某个策略。
- **`httpx_auth.InjectingAuth`** —— 一个 `httpx.Auth`，缓存凭证、按 JWT `exp` / TTL
  刷新、注入 header，并在 `401` 时强制刷新并重试一次请求。
- **`proxy.py`** —— 构建一个 [FastMCP](https://github.com/jlowin/fastmcp) 代理
  （`create_proxy` + `ProxyClient` + `StreamableHttpTransport(auth=…)`），把完整的
  MCP 能力（tools、resources、prompts）透明转发，并通过 stdio 提供服务。`stdout`
  专用于 JSON-RPC；所有日志走 `stderr`。

## 开发

```bash
uv sync                 # 安装 dev 依赖
uv run pytest -q        # 52 个测试，含 bearer + api-key 的 stdio<->HTTP 实时 e2e
uv run ruff check .     # lint
uv run pyright          # 类型检查
```

端到端测试（`tests/test_e2e_bearer.py`、`tests/test_e2e_apikey.py`）会启动一个真实的
bearer / api-key 保护的 StreamableHTTP MCP server，并把代理作为子进程通过 stdio
驱动，同时验证成功与被拒绝（错误凭证）的调用。

## 安全说明

- 代理在本地暴露上游 MCP server；请像对待底层工具本身那样对待它。凭证仅保存在
  内存中（外加 AgentCore vault 的服务端存储）。`~/.mcp-identity-proxy-for-aws/workload.json`
  只存储 workload identity 名称和一个随机 user id —— **不含任何 secret**。
- **请使用 HTTPS 端点。** 代理会把凭证注入每个请求；如果端点不是 `https://`（且非
  localhost），凭证会以明文发送。此时代理会打印一条 warning。
- **secret 优先用环境变量，而非 `--token` / `--header`。** 命令行参数会出现在进程表
  （`ps`）和 shell 历史里；请改用 `PROXY_STATIC_TOKEN`。
- **Region：** 如果 `--region` / `AWS_REGION` / `AWS_DEFAULT_REGION` 都未设置，代理会
  默认用 `us-east-1`。请显式设置，避免出现令人困惑的跨区域 `AccessDenied` /
  `ResourceNotFound` 错误。
- **`ON_BEHALF_OF_TOKEN_EXCHANGE`** 虽然被 `--oauth-flow` 接受，但目前是实验性/未验证
  的；已测试的流程是 `M2M` 和 `USER_FEDERATION`。
- 建议使用最小权限 IAM 策略，精确限定到具体的 credential provider。
- 这不是 AWS 官方项目。在生产使用前请充分测试。

## 许可证

Apache-2.0
