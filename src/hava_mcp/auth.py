"""Where the Hava API token comes from.

Exactly one source, by design: the **HAVA_API_TOKEN** environment variable.

That is what the Claude plugin's install-time "Hava API token" field and the
``.mcpb`` extension's ``user_config.api_token`` are delivered as, and what you
export in a development checkout or CI. There is no login command, no keychain
write and no credential file: the host application already stores the
configured value securely and injects it into the server's environment.

``HAVA_ENDPOINT`` optionally overrides the API base URL for self-hosted Hava -
the same variable name Hava's own CLI and Terraform provider use.
"""

from __future__ import annotations

import os
from typing import NamedTuple

from .errors import HavaConfigError

TOKEN_ENV_VAR = "HAVA_API_TOKEN"
ENDPOINT_ENV_VAR = "HAVA_ENDPOINT"
DEFAULT_BASE_URL = "https://api.hava.io"

TOKEN_PAGE_URL = "https://app.hava.io/settings/profile"

# The id of the local marketplace build. Installing from the organization
# changes only the part after "@"; `claude plugin list` prints the real one.
PLUGIN_ID = "cloudzone-hava@synced"

SETUP_INSTRUCTIONS = f"""\
This server needs a Hava API token. Set it from Claude Code in a terminal:

  1. Open Terminal (or iTerm) on your own machine.
  2. Run:  claude
  3. In that Claude Code session, type:

         /plugin configure {PLUGIN_ID}

  4. Paste the token into the "Hava API token" field and confirm.
  5. Restart Claude or open a new session.

Get the token from {TOKEN_PAGE_URL}
(avatar in app.hava.io -> Personal Settings -> API Tokens -> Display Token).

That is the supported way; there is no login tool and no other route.

Installing never prompts for this, in any host: an unconfigured plugin's MCP
server is simply not started, which is why no hava_* tools appeared. The
install did not fail, and reinstalling will not help.

Ran the command and still seeing this? Two causes:

  - Wrong plugin id. Each installed copy keeps its own saved options. Run
    `claude plugin list` and configure the id shown as loaded.

  - Two copies of the plugin installed. Only one loads, and saved options do
    not carry across them; `claude plugin list` ends with a warning naming the
    copy that takes precedence. Uninstall the redundant one, then configure
    again.

To check what was saved:
    claude plugin configure {PLUGIN_ID} --json
An empty api_token counts as "configured" but cannot authenticate; that is what
`claude plugin install -y` records.

API access requires a Hava Teams/Business plan or a self-hosted install.\
"""


class Credential(NamedTuple):
    """A resolved token plus where it came from."""

    token: str
    origin: str  # "env:HAVA_API_TOKEN" | "explicit"


def _clean(value: str | None) -> str | None:
    """Reject blanks and placeholders a host left unexpanded."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("${") and text.endswith("}"):
        return None
    return text


def default_base_url() -> str:
    return (_clean(os.environ.get(ENDPOINT_ENV_VAR)) or DEFAULT_BASE_URL).rstrip("/")


def resolve() -> Credential | None:
    """Return the configured token, or ``None`` if there is not one."""
    token = _clean(os.environ.get(TOKEN_ENV_VAR))
    return Credential(token, f"env:{TOKEN_ENV_VAR}") if token else None


def require() -> str:
    """Return the configured token or raise with setup instructions."""
    credential = resolve()
    if credential is None:
        raise HavaConfigError(f"No Hava API token configured.\n\n{SETUP_INSTRUCTIONS}")
    return credential.token


def mask(token: str) -> str:
    """Show just enough of a token to recognise it, never enough to use it."""
    token = token or ""
    if len(token) <= 10:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]} ({len(token)} chars)"


def account_id_from_token(token: str) -> str | None:
    """Pull the Hava account ID out of an API token.

    Hava's API has no endpoint that reports which account a token belongs to,
    yet every /accounts/{account_id}/... route needs one. The token is a JWT
    whose `sub` claim is that account ID (verified against the live API), so
    decode it rather than making callers hunt for it.

    Returns ``None`` for anything that is not a readable JWT; callers then fall
    back to an explicit account_id argument.
    """
    import base64
    import json as _json

    try:
        payload = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claim = _json.loads(decoded).get("sub")
    except Exception:
        return None
    return str(claim) if claim else None
