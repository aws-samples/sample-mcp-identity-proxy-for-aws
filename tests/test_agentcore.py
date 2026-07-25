"""Tests for AgentCore Identity strategies with a mocked IdentityClient."""

from __future__ import annotations

import json

import pytest

from mcp_identity_proxy_for_aws.auth.agentcore import (
    AgentCoreApiKeyStrategy,
    AgentCoreCredentialSource,
    AgentCoreOAuthStrategy,
    resolve_region,
)


class FakeIdentityClient:
    """Records calls and returns canned AgentCore Identity responses."""

    def __init__(self, region):
        self.region = region
        self.created_workloads = 0
        self.workload_calls: list[dict] = []
        self.get_token_calls: list[dict] = []
        self.get_api_key_calls: list[dict] = []
        self._token_counter = 0

    def create_workload_identity(self, name=None):
        self.created_workloads += 1
        return {"name": name or "workload-generated"}

    def get_workload_access_token(self, workload_name, user_token=None, user_id=None):
        self.workload_calls.append(
            {"workload_name": workload_name, "user_token": user_token, "user_id": user_id}
        )
        return {"workloadAccessToken": f"wtoken-for-{workload_name}"}

    async def get_token(self, **kwargs):
        self.get_token_calls.append(kwargs)
        self._token_counter += 1
        return f"oauth-token-{self._token_counter}"

    async def get_api_key(self, **kwargs):
        self.get_api_key_calls.append(kwargs)
        return "api-key-value"


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mcp_identity_proxy_for_aws.auth.agentcore.IdentityClient",
        FakeIdentityClient,
    )
    return AgentCoreCredentialSource(region="us-east-1", config_dir=tmp_path)


async def test_workload_identity_created_and_persisted(source, tmp_path):
    token = await source.workload_token()
    assert token.startswith("wtoken-for-")
    # A workload identity was created and persisted to config with a user id.
    assert source.client.created_workloads == 1
    config = json.loads((tmp_path / "workload.json").read_text())
    assert config["workload_identity_name"]
    assert config["user_id"]
    # user_id (not user_token) used for the dev pattern.
    assert source.client.workload_calls[0]["user_id"] is not None
    assert source.client.workload_calls[0]["user_token"] is None


async def test_workload_identity_reused_from_config(tmp_path, monkeypatch):
    (tmp_path / "workload.json").write_text(
        json.dumps({"workload_identity_name": "existing-wl", "user_id": "abc123"})
    )
    monkeypatch.setattr("mcp_identity_proxy_for_aws.auth.agentcore.IdentityClient", FakeIdentityClient)
    src = AgentCoreCredentialSource(region="us-east-1", config_dir=tmp_path)
    await src.workload_token()
    # No new workload identity created; existing one reused.
    assert src.client.created_workloads == 0
    assert src.client.workload_calls[0]["workload_name"] == "existing-wl"
    assert src.client.workload_calls[0]["user_id"] == "abc123"


async def test_user_token_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr("mcp_identity_proxy_for_aws.auth.agentcore.IdentityClient", FakeIdentityClient)
    src = AgentCoreCredentialSource(
        region="us-east-1", config_dir=tmp_path, workload_identity_name="wl", user_token="jwt-token"
    )
    await src.workload_token()
    call = src.client.workload_calls[0]
    assert call["user_token"] == "jwt-token"
    assert call["user_id"] is None


async def test_oauth_strategy_fetches_bearer(source):
    strat = AgentCoreOAuthStrategy(
        source=source, provider_name="my-provider", scopes=["scope-a"], auth_flow="M2M"
    )
    cred = await strat.fetch()
    assert cred.header_name == "Authorization"
    assert cred.header_value == "Bearer oauth-token-1"
    call = source.client.get_token_calls[0]
    assert call["provider_name"] == "my-provider"
    assert call["auth_flow"] == "M2M"
    assert call["scopes"] == ["scope-a"]
    assert call["agent_identity_token"].startswith("wtoken-for-")
    assert call["force_authentication"] is False


async def test_oauth_strategy_force_refresh_sets_force_authentication(source):
    strat = AgentCoreOAuthStrategy(source=source, provider_name="p", auth_flow="M2M")
    await strat.fetch(force_refresh=True)
    assert source.client.get_token_calls[0]["force_authentication"] is True


async def test_apikey_strategy_raw_header(source):
    strat = AgentCoreApiKeyStrategy(source=source, provider_name="key-provider")
    cred = await strat.fetch()
    assert cred.header_name == "x-api-key"
    assert cred.scheme is None
    assert cred.header_value == "api-key-value"
    assert source.client.get_api_key_calls[0]["provider_name"] == "key-provider"


def test_resolve_region(monkeypatch):
    assert resolve_region("eu-west-1") == "eu-west-1"
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    assert resolve_region(None) == "us-east-1"
    monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
    assert resolve_region(None) == "ap-southeast-2"
