"""Tests for the Credential model and JWT expiry parsing."""

from __future__ import annotations

import base64
import json
import time

from mcp_identity_proxy_for_aws.credential import Credential, jwt_expiry


def _make_jwt(claims: dict) -> str:
    def b64(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


def test_header_value_with_scheme():
    cred = Credential(value="abc", scheme="Bearer")
    assert cred.header_value == "Bearer abc"


def test_header_value_raw():
    cred = Credential(value="secret", header_name="x-api-key", scheme=None)
    assert cred.header_value == "secret"
    assert cred.header_name == "x-api-key"


def test_is_expired_none_never_expires():
    assert Credential(value="x", expires_at=None).is_expired() is False


def test_is_expired_true_when_past():
    assert Credential(value="x", expires_at=time.time() - 10).is_expired() is True


def test_is_expired_respects_skew():
    # Expires in 30s but skew is 60s -> treated as expired.
    assert Credential(value="x", expires_at=time.time() + 30).is_expired(skew_seconds=60) is True
    # Expires in 120s with 60s skew -> still valid.
    assert Credential(value="x", expires_at=time.time() + 120).is_expired(skew_seconds=60) is False


def test_jwt_expiry_extracts_exp():
    token = _make_jwt({"exp": 1234567890, "sub": "user"})
    assert jwt_expiry(token) == 1234567890.0


def test_jwt_expiry_non_jwt_returns_none():
    assert jwt_expiry("not-a-jwt") is None
    assert jwt_expiry("opaque-token-value") is None


def test_jwt_expiry_missing_exp_returns_none():
    token = _make_jwt({"sub": "user"})
    assert jwt_expiry(token) is None
