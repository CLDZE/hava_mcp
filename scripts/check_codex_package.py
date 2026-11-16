#!/usr/bin/env python3
"""Validate an assembled Codex (Agent Plugins) package before it is zipped.

    check_codex_package.py <package-dir> [--expect-version X.Y.Z] [--out-dir DIR]

The Agent Plugins schemas are not fetched at build time - the sandbox has no
network and a build must not depend on one - so the rules they encode are
checked here instead. Failures exit 1; warnings are printed and tolerated.

What it enforces, and why each one bites:

* ``plugin.json`` at the package root, with the exact ``$schema`` of the 1.0.0
  plugin schema, a kebab-case ``name`` (hosts use it as the plugin identity and
  component namespace, and it must never change), a semver ``version`` matching
  the build, and no property the schema does not allow - its root is
  ``additionalProperties: false``.
* Every path under ``extensions."com.openai"`` starts with ``./``, stays inside
  the package and exists. A missing icon or onboarding skill is silently
  ignored by the host, so it would ship broken.
* ``mcp.json`` declares a transport ``type`` for every server. Claude's
  ``.mcp.json`` shape without one is accepted by nothing and the tools simply
  never appear.
* A stdio server's ``${PLUGIN_ROOT}``-relative files exist, its ``env`` holds
  neither ``PLUGIN_ROOT``/``PLUGIN_DATA`` (reserved) nor an unexpanded
  ``${user_config.*}`` placeholder (Codex has no install-time prompt, so one
  would reach the server as a literal string), and nothing that looks like a
  real secret.
* Each ``skills/<dir>/SKILL.md`` has frontmatter whose ``name`` equals its
  folder, plus a ``description`` - Codex discovers skills by folder and selects
  them by description.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"

# The plugin schema's root is additionalProperties: false.
PLUGIN_KEYS = {
    "$schema", "name", "version", "description", "author", "homepage",
    "repository", "license", "keywords", "extensions",
}
AUTHOR_KEYS = {"name", "email", "url"}
NAME_RX = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SEMVER_RX = re.compile(r"^(\d+)\.(\d+)\.(\d+)")
TRANSPORTS = {"stdio", "streamable-http", "sse"}
CWD_RX = re.compile(r"^(?:\./|\$\{PLUGIN_ROOT\}(?:/|$)|\$\{PLUGIN_DATA\}(?:/|$))")
SECRET_RX = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|eyJ[A-Za-z0-9_-]{20,}"
    r"|Bearer\s+[A-Za-z0-9._-]{20,})"
)
JUNK = {".DS_Store", "Thumbs.db"}

fails: list[str] = []
warns: list[str] = []


def semver(value: str) -> tuple[int, int, int] | None:
    match = SEMVER_RX.match(value or "")
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def check_asset_path(pkg: Path, where: str, value: str) -> None:
    """A path inside extensions."com.openai": ./-prefixed, contained, present."""
    if not value.startswith("./"):
        fails.append(f"{where}: {value!r} must start with './'")
        return
    target = (pkg / value[2:]).resolve()
    if not str(target).startswith(str(pkg.resolve())):
        fails.append(f"{where}: {value!r} escapes the package root")
    elif not target.exists():
        fails.append(f"{where}: {value!r} does not exist in the package")


def check_plugin_manifest(pkg: Path, expect_version: str | None) -> tuple[str, str]:
    path = pkg / "plugin.json"
    if not path.is_file():
        fails.append("plugin.json missing from the package root")
        return "", ""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report, do not raise
        fails.append(f"plugin.json is not valid JSON: {exc}")
        return "", ""

    if manifest.get("$schema") != PLUGIN_SCHEMA:
        fails.append(f"plugin.json $schema must be exactly {PLUGIN_SCHEMA}")
    unknown = set(manifest) - PLUGIN_KEYS
    if unknown:
        fails.append(f"plugin.json has properties the schema forbids: {sorted(unknown)}")

    name = manifest.get("name") or ""
    if not NAME_RX.match(name) or not 1 <= len(name) <= 64:
        fails.append(f"plugin.json name {name!r} does not match the schema's pattern")

    version = manifest.get("version") or ""
    if not semver(version):
        fails.append(f"plugin.json version {version!r} is not semver")
    elif expect_version and version != expect_version:
        fails.append(f"plugin.json version {version} != pyproject {expect_version}")

    if not (manifest.get("description") or "").strip():
        fails.append("plugin.json description missing")

    author = manifest.get("author")
    if author is not None:
        if not isinstance(author, dict) or set(author) - AUTHOR_KEYS:
            fails.append("plugin.json author allows only name/email/url")

    openai = ((manifest.get("extensions") or {}).get("com.openai")) or {}
    if not openai:
        warns.append(
            "no extensions.\"com.openai\": the plugin shows as its bare name, "
            "with no icon or category, in the Plugins Directory"
        )
    for key in ("apps", "hooks", "onboardingSkill"):
        value = openai.get(key)
        if isinstance(value, str):
            check_asset_path(pkg, f'extensions."com.openai".{key}', value)
    interface = openai.get("interface") or {}
    for key in ("composerIcon", "logo"):
        if key in interface:
            check_asset_path(pkg, f'interface.{key}', interface[key])
    for shot in interface.get("screenshots") or []:
        check_asset_path(pkg, "interface.screenshots[]", shot)
    if (pkg / ".codex-plugin" / "plugin.json").exists() and openai:
        fails.append(
            "both extensions.\"com.openai\" and .codex-plugin/plugin.json are "
            "present; the inline object replaces the overlay rather than "
            "merging, so one of them is dead weight"
        )
    return name, version


def check_mcp_config(pkg: Path) -> bool:
    path = pkg / "mcp.json"
    if not path.is_file():
        warns.append("no mcp.json: the plugin contributes skills only")
        return False
    raw = path.read_text(encoding="utf-8")
    if SECRET_RX.search(raw):
        fails.append("mcp.json contains what looks like a literal secret")
    try:
        config = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        fails.append(f"mcp.json is not valid JSON: {exc}")
        return False
    if config.get("$schema") != MCP_SCHEMA:
        fails.append(f"mcp.json $schema must be exactly {MCP_SCHEMA}")

    servers = config.get("mcpServers") or {}
    if not servers:
        fails.append("mcp.json declares no servers")
    has_stdio = False
    for key, server in servers.items():
        transport = server.get("type")
        if transport not in TRANSPORTS:
            fails.append(f"server {key}: type {transport!r} must be one of {sorted(TRANSPORTS)}")
            continue
        if transport == "stdio":
            has_stdio = True
            if not (server.get("command") or "").strip():
                fails.append(f"server {key}: stdio needs a command")
            cwd = server.get("cwd")
            if cwd is not None and not CWD_RX.match(cwd):
                fails.append(f"server {key}: cwd {cwd!r} must be ./, ${{PLUGIN_ROOT}} or ${{PLUGIN_DATA}} rooted")
            for name, value in (server.get("env") or {}).items():
                if name in ("PLUGIN_ROOT", "PLUGIN_DATA"):
                    fails.append(f"server {key}: env may not set {name} (the host owns it)")
                if "${user_config." in str(value):
                    fails.append(
                        f"server {key}: env {name} still carries a Claude "
                        "${user_config.*} placeholder, which Codex never expands"
                    )
            for arg in server.get("args") or []:
                if arg.startswith("${PLUGIN_ROOT}/"):
                    target = pkg / arg[len("${PLUGIN_ROOT}/"):]
                    if not target.exists():
                        fails.append(f"server {key}: {arg} is not in the package")
                    elif target.suffix in (".sh", ".mjs", ".js", ".py") and not os.access(target, os.X_OK):
                        warns.append(f"server {key}: {arg} is not executable (fine while `command` is a shell)")
        else:
            if not (server.get("url") or "").strip():
                fails.append(f"server {key}: {transport} needs a url")
    return has_stdio


def check_skills(pkg: Path) -> list[str]:
    root = pkg / "skills"
    if not root.is_dir():
        warns.append("no skills/: the plugin contributes tool names only")
        return []
    found = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        skill = entry / "SKILL.md"
        if not skill.is_file():
            fails.append(f"skills/{entry.name}/SKILL.md missing")
            continue
        text = skill.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---", text, re.S)
        if not match:
            fails.append(f"skills/{entry.name}/SKILL.md has no frontmatter")
            continue
        fields = {}
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            if value and not line.startswith((" ", "\t")):
                fields[key.strip()] = value.strip().strip('"').strip("'")
        if fields.get("name") != entry.name:
            fails.append(
                f"skills/{entry.name}/SKILL.md name {fields.get('name')!r} != folder {entry.name!r}"
            )
        if not fields.get("description"):
            fails.append(f"skills/{entry.name}/SKILL.md needs a description")
        found.append(entry.name)
    return found


def check_contents(pkg: Path) -> None:
    files = [p for p in pkg.rglob("*") if p.is_file()]
    junk = [p for p in files if p.name in JUNK or p.suffix == ".pyc" or "__pycache__" in p.parts]
    if junk:
        fails.append(f"{len(junk)} junk file(s) staged, e.g. {junk[0].relative_to(pkg)}")
    if len(files) > 5000:
        fails.append(f"{len(files)} files > 5,000")
    total = sum(p.stat().st_size for p in files)
    if total > 200e6:
        fails.append(f"{total / 1e6:.0f} MB > 200 MB")
    if not (pkg / "README.md").is_file():
        warns.append("no README.md")
    elif len(re.findall(r"\b\w+\b", re.sub(r"```.*?```", "", (pkg / "README.md").read_text(encoding="utf-8"), flags=re.S))) < 40:
        warns.append("README has < 40 words outside code blocks")
    print(f"    {len(files)} files, {total / 1e6:.2f} MB")


def check_previous(out_dir: Path, name: str, version: str) -> None:
    """A newer upload has to carry a higher version than the newest on disk."""
    stem = name if re.search(r"(^|-)mcp($|-)", name) else f"{name}-mcp"
    previous = sorted(
        out_dir.glob(f"{stem}-codex-v*.zip"),
        key=lambda p: semver(re.search(r"-v([0-9.]+)\.zip$", p.name).group(1)) or (0, 0, 0),
    )
    older = [p for p in previous if p.name != f"{stem}-codex-v{version}.zip"]
    if not older:
        return
    found = re.search(r"-v([0-9.]+)\.zip$", older[-1].name).group(1)
    if semver(version) and semver(found) and semver(version) <= semver(found):
        warns.append(f"version {version} <= previous {found} ({older[-1].name}): bump it")
    else:
        print(f"    version {found} -> {version}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package")
    parser.add_argument("--expect-version")
    parser.add_argument("--out-dir")
    args = parser.parse_args()

    pkg = Path(args.package).resolve()
    if not pkg.is_dir():
        print(f"FAIL: {pkg} is not a directory")
        return 1

    name, version = check_plugin_manifest(pkg, args.expect_version)
    has_stdio = check_mcp_config(pkg)
    skills = check_skills(pkg)
    check_contents(pkg)
    if args.out_dir and name and version:
        check_previous(Path(args.out_dir), name, version)

    if has_stdio:
        warns.append(
            "bundled stdio server: runs only where the member runs Codex, and "
            "the package is not eligible for the public plugin directory "
            "(workspace publishing is fine)"
        )

    for warn in warns:
        print(f"    WARN: {warn}")
    for fail in fails:
        print(f"    FAIL: {fail}")
    if fails:
        return 1
    print(f"    {name} {version} valid: {len(skills)} skill(s) [{', '.join(skills)}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
