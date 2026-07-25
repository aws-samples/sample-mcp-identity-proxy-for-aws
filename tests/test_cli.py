"""Tests for CLI parsing, especially scope/resource/audience merging."""

from __future__ import annotations

from mcp_identity_proxy_for_aws.cli import _merge_list, build_parser, config_from_args

ENDPOINT = "https://example.com/mcp"


def test_merge_list_splits_and_dedupes():
    assert _merge_list(["a", "b"], ["b c", "d,e"]) == ["a", "b", "c", "d", "e"]
    assert _merge_list(None, []) == []
    assert _merge_list(["  x  ", "x"]) == ["x"]


def _cfg(argv):
    return config_from_args(build_parser().parse_args(argv))


def test_scopes_single_flag_multiple_values():
    cfg = _cfg([ENDPOINT, "--scopes", "aud-websearchgateway", "ticket:read"])
    assert cfg.scopes == ["aud-websearchgateway", "ticket:read"]


def test_scopes_single_delimited_string():
    cfg = _cfg([ENDPOINT, "--scopes", "aud-websearchgateway ticket:read"])
    assert cfg.scopes == ["aud-websearchgateway", "ticket:read"]

    cfg2 = _cfg([ENDPOINT, "--scopes", "a,b,c"])
    assert cfg2.scopes == ["a", "b", "c"]


def test_scope_and_scopes_merge_deduped():
    cfg = _cfg([ENDPOINT, "--scope", "a", "--scope", "b", "--scopes", "b", "c"])
    assert cfg.scopes == ["a", "b", "c"]


def test_repeatable_scope_still_works():
    cfg = _cfg([ENDPOINT, "--scope", "a", "--scope", "b"])
    assert cfg.scopes == ["a", "b"]


def test_resources_and_audiences_plural():
    cfg = _cfg([ENDPOINT, "--audiences", "aud1", "aud2", "--resources", "r1,r2"])
    assert cfg.audiences == ["aud1", "aud2"]
    assert cfg.resources == ["r1", "r2"]
