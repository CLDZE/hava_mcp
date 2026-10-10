"""The hava-mcp command line: status reporting only."""

from __future__ import annotations

import base64
import json

import httpx
import respx

from hava_mcp import auth
from hava_mcp.cli import build_parser, cmd_status, main

BASE_URL = "https://api.hava.test"
SOURCES = {"total_size": 2, "results": [{"id": "s1"}, {"id": "s2"}]}


def _args():
    return build_parser().parse_args(["--endpoint", BASE_URL, "status"])


def test_status_exits_nonzero_when_no_token_is_configured(capsys):
    assert cmd_status(_args()) == 1
    assert "NO TOKEN CONFIGURED" in capsys.readouterr().out


@respx.mock
def test_status_reports_the_token_and_verifies_it(monkeypatch, capsys):
    body = base64.urlsafe_b64encode(json.dumps({"sub": "acct-1"}).encode()).decode().rstrip("=")
    monkeypatch.setenv(auth.TOKEN_ENV_VAR, f"h.{body}.s")
    respx.get(f"{BASE_URL}/sources").mock(return_value=httpx.Response(200, json=SOURCES))
    assert cmd_status(_args()) == 0
    out = capsys.readouterr().out
    assert "credential origin: env:HAVA_API_TOKEN" in out
    assert "account id:        acct-1" in out
    assert "live check:        OK (2 source(s))" in out


@respx.mock
def test_status_reports_a_rejected_token(monkeypatch, capsys):
    monkeypatch.setenv(auth.TOKEN_ENV_VAR, "bad-token")
    respx.get(f"{BASE_URL}/sources").mock(return_value=httpx.Response(401))
    assert cmd_status(_args()) == 1
    assert "FAILED" in capsys.readouterr().out


def test_login_and_logout_commands_no_longer_exist(capsys):
    """They were replaced by install-time configuration."""
    for removed in ("login", "logout"):
        try:
            main(["--endpoint", BASE_URL, removed])
        except SystemExit as exc:
            assert exc.code != 0
        else:  # pragma: no cover
            raise AssertionError(f"{removed} should no longer be a command")
