"""Shared fixtures: an isolated environment and a stub Hava client."""

from __future__ import annotations

import os

import pytest

# hava_mcp.app reads HAVA_MCP_READ_ONLY into a module constant at import time,
# which is during collection, before any fixture runs. Drop the ambient values
# here, ahead of the first hava_mcp import, so the shell cannot change which
# tools get registered.
os.environ.pop("HAVA_MCP_READ_ONLY", None)
os.environ.pop("HAVA_MCP_HOST", None)

from hava_mcp import auth, client as client_module  # noqa: E402

BASE_URL = "https://api.hava.test"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """No ambient Hava configuration leaks into a test."""
    monkeypatch.delenv(auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(auth.ENDPOINT_ENV_VAR, raising=False)
    monkeypatch.delenv(auth.HOST_ENV_VAR, raising=False)
    client_module.set_client(None)
    yield
    client_module.set_client(None)


@pytest.fixture
def hava_client():
    """Install a client whose token is fixed, pointed at the test base URL."""
    instance = client_module.HavaClient(token="test-token", base_url=BASE_URL)
    client_module.set_client(instance)
    return instance
