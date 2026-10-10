"""Shared fixtures: an isolated environment and a stub Hava client."""

from __future__ import annotations

import pytest

from hava_mcp import auth, client as client_module

BASE_URL = "https://api.hava.test"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """No ambient Hava configuration leaks into a test."""
    monkeypatch.delenv(auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(auth.ENDPOINT_ENV_VAR, raising=False)
    client_module.set_client(None)
    yield
    client_module.set_client(None)


@pytest.fixture
def hava_client():
    """Install a client whose token is fixed, pointed at the test base URL."""
    instance = client_module.HavaClient(token="test-token", base_url=BASE_URL)
    client_module.set_client(instance)
    return instance
