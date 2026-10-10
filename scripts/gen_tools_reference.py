#!/usr/bin/env python3
"""Regenerate skills/hava-diagrams/references/tools.md from the live server.

Every tool must appear in a group; an ungrouped tool fails the build rather
than silently going undocumented.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

GROUPS: list[tuple[str, list[str]]] = [
    ("Find diagrams", ["hava_list_diagrams", "hava_find_diagrams"]),
    ("Inspect a diagram", ["hava_get_diagram", "hava_list_diagram_versions"]),
    ("Cloud resources", ["hava_list_resources", "hava_export_resources"]),
    ("Export", ["hava_export_diagram", "hava_get_job"]),
    ("Custom diagrams", ["hava_create_diagram", "hava_update_diagram", "hava_delete_diagram"]),
    ("Notes", ["hava_list_diagram_notes", "hava_create_diagram_note", "hava_delete_diagram_note"]),
    ("Shares (public links)", ["hava_list_diagram_shares", "hava_get_diagram_share",
                               "hava_create_diagram_share", "hava_update_diagram_share"]),
    ("Reports", ["hava_list_reports", "hava_get_report", "hava_export_report"]),
    ("Cloud sources", ["hava_list_sources", "hava_get_source", "hava_sync_source",
                       "hava_rename_source", "hava_delete_source"]),
    ("Teams and members", ["hava_list_teams", "hava_get_team", "hava_create_team",
                           "hava_update_team", "hava_delete_team", "hava_add_team_member",
                           "hava_invite_team_member", "hava_remove_team_member"]),
    ("Projects and members", ["hava_list_projects", "hava_get_project", "hava_create_project",
                              "hava_update_project", "hava_delete_project",
                              "hava_add_project_member", "hava_remove_project_member"]),
    ("API tokens", ["hava_list_api_tokens", "hava_delete_api_token"]),
    ("Authentication", ["hava_auth_status"]),
]

OUT = pathlib.Path(__file__).resolve().parent.parent / "skills/hava-diagrams/references/tools.md"


def param_type(schema: object) -> str:
    if not isinstance(schema, dict):
        return "any"
    if "type" in schema:
        kind = schema["type"]
        kind = "|".join(kind) if isinstance(kind, list) else kind
        if kind == "array" and isinstance(schema.get("items"), dict):
            kind = f"array<{param_type(schema['items'])}>"
        return kind
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return "|".join(sorted({param_type(x) for x in schema[key]}))
    return "enum" if "enum" in schema else "any"


def clip(text: str, limit: int = 200) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def main() -> int:
    from hava_mcp import __version__
    from hava_mcp.server import mcp

    tools = await mcp.list_tools()
    by_name = {t.name: t for t in tools}
    grouped = {n for _, names in GROUPS for n in names}
    missing = sorted(set(by_name) - grouped)
    if missing:
        print(f"ERROR: ungrouped tools, add them to GROUPS: {missing}", file=sys.stderr)
        return 1

    def mark(tool: object) -> str:
        ann = tool.annotations
        if ann.destructiveHint:
            return " `[!]`"
        return "" if ann.readOnlyHint else " `[W]`"

    lines = [
        "# Hava MCP tools reference",
        "",
        f"Generated from the bundled server (hava-mcp {__version__}, {len(tools)} tools). "
        "Descriptions are the server's own; parameters are top-level inputs "
        "(`*` = required). `[W]` changes something in Hava, `[!]` is destructive "
        "and needs `confirm=true`.",
        "",
        "## Contents",
        "",
    ]
    for group, names in GROUPS:
        present = [f"`{n}`{mark(by_name[n])}" for n in names if n in by_name]
        if present:
            lines.append(f"- **{group}**: " + ", ".join(present))
    lines.append("")

    for group, names in GROUPS:
        present = [n for n in names if n in by_name]
        if not present:
            continue
        lines += [f"## {group}", ""]
        for name in present:
            tool = by_name[name]
            lines += [f"### `{name}`{mark(tool)}", "",
                      (tool.description or "").strip().split("\n")[0], ""]
            schema = tool.inputSchema or {}
            props = schema.get("properties", {})
            required = set(schema.get("required", []))
            if props:
                lines += ["Parameters:", ""]
                for pname, pschema in props.items():
                    enum = None
                    if isinstance(pschema, dict):
                        enum = pschema.get("enum") or (pschema.get("items") or {}).get("enum")
                    suffix = f" — one of: {', '.join(map(str, enum[:12]))}" if enum else ""
                    desc = pschema.get("description", "") if isinstance(pschema, dict) else ""
                    lines.append(
                        f"- `{pname}`{'*' if pname in required else ''} "
                        f"({param_type(pschema)}){suffix}: {clip(desc)}"
                    )
                lines.append("")

    OUT.write_text("\n".join(lines) + "\n")
    print(f"    {OUT.relative_to(OUT.parent.parent.parent.parent)} - {len(tools)} tools")
    return 0


raise SystemExit(asyncio.run(main()))
