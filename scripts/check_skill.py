#!/usr/bin/env python3
"""Verify the skill still matches the server's actual tools.

Catches the ways skill docs rot as tools change: a tool that no longer exists,
a new tool nobody documented, a parameter renamed out from under the prose, a
stale tool count, or a destructive tool missing from the confirm-first list.

Exit code 1 on any problem, so scripts/build.sh and CI can gate on it.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills/hava-diagrams/SKILL.md"
TOOLS_MD = ROOT / "skills/hava-diagrams/references/tools.md"
MANIFEST = ROOT / "packaging/plugin/.claude-plugin/plugin.json"

# Words in backticks that are not tools or parameters.
NOT_PARAMS = {
    "markdown", "json", "png", "pdf", "vsdx", "csv", "infrastructure", "extended",
    "security", "container", "list", "field", "value", "pending", "importing",
    "active", "type", "name", "source", "region", "vpc", "cluster", "roles",
    "uv", "claude", "login", "logout", "status", "serve", "true", "false",
}


def manifest_config() -> tuple[dict, set[str]]:
    """The plugin manifest and its userConfig option names.

    The skill talks the user through /plugin configure, so it legitimately
    names config options that are not tool parameters - and must name the ones
    the manifest actually declares.
    """
    manifest = json.loads(MANIFEST.read_text())
    return manifest, set(manifest.get("userConfig", {}))


async def main() -> int:
    from hava_mcp import __version__
    from hava_mcp.server import mcp

    tools = await mcp.list_tools()
    live = {t.name: t for t in tools}
    params = {n: set((t.inputSchema or {}).get("properties", {})) for n, t in live.items()}
    skill = SKILL.read_text()
    reference = TOOLS_MD.read_text()
    problems: list[str] = []

    # 1. tools.md covers exactly the live tool set
    documented = set(re.findall(r"^### `(hava_[a-z_]+)`", reference, re.M))
    for name in sorted(set(live) - documented):
        problems.append(f"tools.md is missing `{name}` - re-run scripts/gen_tools_reference.py")
    for name in sorted(documented - set(live)):
        problems.append(f"tools.md documents `{name}`, which no longer exists")

    # 2. the stated tool count matches reality
    stated = re.search(r"(\d+)\s+tools\)", reference)
    if stated and int(stated.group(1)) != len(live):
        problems.append(
            f"tools.md says {stated.group(1)} tools, server has {len(live)}"
        )
    if f"hava-mcp {__version__}" not in reference:
        problems.append(f"tools.md was generated for a different version (expected {__version__})")

    # 3. every tool the skill names exists
    cited = set(re.findall(r"`(hava_[a-z_]+)`", skill))
    for name in sorted(cited - set(live)):
        problems.append(f"SKILL.md references `{name}`, which is not a tool")

    # 4. the skill's own tool count claim
    claimed = re.search(r"all (\d+) tools", skill)
    if claimed and int(claimed.group(1)) != len(live):
        problems.append(f"SKILL.md claims {claimed.group(1)} tools, server has {len(live)}")

    # 5. parameters named in the skill actually exist somewhere
    manifest, options = manifest_config()
    all_params = {p for ps in params.values() for p in ps} | options
    backticked = set(re.findall(r"`([a-z_]{3,})`", skill)) - cited - NOT_PARAMS
    for token in sorted(backticked - all_params):
        problems.append(f"SKILL.md mentions `{token}`, which is not a parameter of any tool")

    # 6. every destructive tool is called out as needing confirmation
    destructive = {n for n, t in live.items() if t.annotations.destructiveHint}
    section = skill.split("## Confirm before these", 1)
    if len(section) < 2:
        problems.append("SKILL.md has no 'Confirm before these' section")
    else:
        body = section[1].split("\n## ", 1)[0]
        for name in sorted(destructive - set(re.findall(r"`(hava_[a-z_]+)`", body))):
            problems.append(f"destructive tool `{name}` is not listed under 'Confirm before these'")

    # 7. tools that require a confirm flag must actually have one
    for name in sorted(destructive):
        if "confirm" not in params[name]:
            problems.append(f"`{name}` is marked destructive but has no `confirm` parameter")

    # 8. no references to retired auth tooling
    for retired in ("hava_login", "hava_logout", "hava-mcp login", "keychain", "api_token file"):
        for label, text in (("SKILL.md", skill), ("tools.md", reference)):
            if retired in text:
                problems.append(f"{label} still references retired '{retired}'")

    if problems:
        print(f"skill check FAILED ({len(problems)} problem(s)):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(
        f"    skill check OK - {len(live)} tools, {len(destructive)} destructive, "
        f"{len(cited)} cited in SKILL.md"
    )
    return 0


raise SystemExit(asyncio.run(main()))
