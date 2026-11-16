"""Token resolution: one variable, no fallbacks, nothing stored."""

from __future__ import annotations

import base64
import json

import pytest

from hava_mcp import auth
from hava_mcp.errors import HavaConfigError


def test_token_comes_from_the_configured_variable(monkeypatch):
    monkeypatch.setenv(auth.TOKEN_ENV_VAR, "configured-token")
    assert auth.resolve() == ("configured-token", "env:HAVA_API_TOKEN")


def test_no_token_configured_resolves_to_none():
    assert auth.resolve() is None


def test_require_explains_how_to_configure_it():
    with pytest.raises(HavaConfigError) as info:
        auth.require()
    message = str(info.value)
    assert "Hava API token" in message
    assert "Personal Settings" in message
    # One route only: the terminal command. No env var, no login, no UI path.
    assert f"claude plugin configure {auth.PLUGIN_ID}" in message
    assert "HAVA_API_TOKEN" not in message


def test_codex_gets_the_route_codex_actually_has(monkeypatch):
    """Codex never prompts, so its launcher reads the token - say so instead."""
    monkeypatch.setenv(auth.HOST_ENV_VAR, "codex")
    with pytest.raises(HavaConfigError) as info:
        auth.require()
    message = str(info.value)
    assert "~/.hava-mcp/api_token" in message
    assert auth.TOKEN_ENV_VAR in message
    # Advice a Codex user cannot follow is worse than none.
    assert "plugin configure" not in message


def test_an_unknown_host_keeps_the_default_advice(monkeypatch):
    monkeypatch.setenv(auth.HOST_ENV_VAR, "something-else")
    assert auth.setup_instructions() is auth.SETUP_INSTRUCTIONS


def test_surrounding_whitespace_is_trimmed(monkeypatch):
    monkeypatch.setenv(auth.TOKEN_ENV_VAR, "  padded-token \n")
    assert auth.resolve().token == "padded-token"


def test_blank_value_counts_as_unconfigured(monkeypatch):
    """An install-time field left empty arrives as an empty string."""
    monkeypatch.setenv(auth.TOKEN_ENV_VAR, "   ")
    assert auth.resolve() is None


def test_unexpanded_placeholder_is_not_treated_as_a_token(monkeypatch):
    """A host that fails to substitute must not yield a literal token."""
    monkeypatch.setenv(auth.TOKEN_ENV_VAR, "${user_config.api_token}")
    assert auth.resolve() is None


@pytest.mark.parametrize("retired", ["HAVA_TOKEN", "HAVA_API_KEY"])
def test_retired_token_aliases_are_ignored(monkeypatch, retired):
    """Only HAVA_API_TOKEN is honoured; the old aliases were removed."""
    monkeypatch.setenv(retired, "alias-token")
    assert auth.resolve() is None


def test_endpoint_defaults_to_hava_saas():
    assert auth.default_base_url() == "https://api.hava.io"


def test_endpoint_variable_overrides_for_self_hosted(monkeypatch):
    monkeypatch.setenv(auth.ENDPOINT_ENV_VAR, "https://hava.internal.example.com/")
    assert auth.default_base_url() == "https://hava.internal.example.com"


@pytest.mark.parametrize("retired", ["HAVA_API_BASE_URL", "HAVA_BASE_URL"])
def test_retired_endpoint_aliases_are_ignored(monkeypatch, retired):
    monkeypatch.setenv(retired, "https://nope.example.com")
    assert auth.default_base_url() == "https://api.hava.io"


def test_mask_never_reveals_the_middle():
    masked = auth.mask("abcdefghijklmnop")
    assert masked.startswith("abcd") and masked.endswith("(16 chars)")
    assert "efghijkl" not in masked
    assert auth.mask("short") == "*****"


def test_account_id_is_read_from_the_token_sub_claim():
    body = base64.urlsafe_b64encode(json.dumps({"sub": "acct-1"}).encode()).decode().rstrip("=")
    assert auth.account_id_from_token(f"h.{body}.s") == "acct-1"


def test_account_id_from_a_non_jwt_is_none():
    assert auth.account_id_from_token("not-a-jwt") is None


def test_nothing_is_written_to_disk(monkeypatch, tmp_path):
    """The server stores no credentials; the host owns the secret."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(auth.TOKEN_ENV_VAR, "configured-token")
    auth.resolve()
    auth.require()
    assert list(tmp_path.iterdir()) == []
