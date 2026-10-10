"""HAVA_MCP_READ_ONLY must keep mutating tools out of the tool list entirely.

Hiding them at registration is stronger than refusing at call time: the model
never sees a tool it must not use. These tests re-import the package in a
subprocess because registration happens at import.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent(
    """
    import asyncio, json
    from hava_mcp.server import mcp
    async def main():
        tools = await mcp.list_tools()
        print(json.dumps({
            "count": len(tools),
            "names": sorted(t.name for t in tools),
            "mutating": sorted(t.name for t in tools if not t.annotations.readOnlyHint),
        }))
    asyncio.run(main())
    """
)


def _list_tools(read_only: bool) -> dict:
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}
    if read_only:
        env["HAVA_MCP_READ_ONLY"] = "1"
    out = subprocess.run(
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, env=env, check=True
    )
    return json.loads(out.stdout)


def test_full_mode_exposes_the_whole_surface():
    result = _list_tools(read_only=False)
    assert result["count"] == 44
    assert "hava_delete_source" in result["names"]
    # Authentication is configuration now, not a tool.
    assert "hava_login" not in result["names"]
    assert "hava_logout" not in result["names"]


def test_read_only_mode_hides_every_mutating_tool():
    result = _list_tools(read_only=True)
    assert result["mutating"] == [], f"still mutating: {result['mutating']}"
    for name in ("hava_delete_source", "hava_delete_diagram", "hava_delete_api_token",
                 "hava_create_diagram_share", "hava_sync_source", "hava_invite_team_member",
                 "hava_export_diagram"):
        assert name not in result["names"], f"{name} should be hidden"


def test_read_only_mode_keeps_the_reading_tools():
    result = _list_tools(read_only=True)
    for name in ("hava_list_diagrams", "hava_find_diagrams", "hava_get_diagram",
                 "hava_list_resources", "hava_list_teams", "hava_auth_status",
                 "hava_get_job", "hava_list_diagram_shares"):
        assert name in result["names"], f"{name} should still be available"


def test_read_only_is_exactly_the_non_mutating_subset():
    """Derive the expected count rather than hard-coding it."""
    full = _list_tools(read_only=False)
    limited = _list_tools(read_only=True)
    read_only_names = sorted(set(full["names"]) - set(full["mutating"]))
    assert limited["names"] == read_only_names
    assert limited["count"] == len(full["names"]) - len(full["mutating"])
