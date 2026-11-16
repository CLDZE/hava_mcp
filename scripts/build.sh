#!/usr/bin/env bash
# Build the distributable artifacts from this checkout.
#
#   scripts/build.sh            -> all three, copied into the MCPB folder
#   scripts/build.sh plugin     -> just the Claude org plugin zip
#   scripts/build.sh mcpb       -> just the Claude Desktop extension
#   scripts/build.sh codex      -> just the Codex plugin zip
#   OUT_DIR=/somewhere scripts/build.sh
#
# Output filenames carry the version from pyproject.toml, e.g.
#   cloudzone-hava-0.3.0.zip
#   hava-mcp-0.3.0.mcpb
#   cloudzone-hava-mcp-codex-v0.4.1.zip
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WHAT="${1:-both}"
OUT_DIR="${OUT_DIR:-$HOME/Documents/GenAIProjects/MCPB}"
VERSION="$(python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
WORK="${TMPDIR:-/tmp}/hava-build-$$"

# uv and npm write to the home cache, which is not always writable.
export UV_CACHE_DIR="${UV_CACHE_DIR:-${TMPDIR:-/tmp}/uv-cache}"
export UV_TOOL_DIR="${UV_TOOL_DIR:-${TMPDIR:-/tmp}/uv-tools}"
export UV_TOOL_BIN_DIR="${UV_TOOL_BIN_DIR:-${TMPDIR:-/tmp}/uv-bin}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-${TMPDIR:-/tmp}/uv-python}"
export npm_config_cache="${npm_config_cache:-${TMPDIR:-/tmp}/npm-cache}"

say() { printf '\n==> %s\n' "$*"; }
trap 'rm -rf "$WORK"' EXIT

say "hava-mcp $VERSION  ->  $OUT_DIR"
mkdir -p "$OUT_DIR"
uv lock --check

# Always build with the project venv. `uv sync` recreates it when it is missing
# or points at an interpreter that no longer exists; a bare system python3
# lacks hava_mcp and fails later with a confusing ModuleNotFoundError.
uv sync --frozen --inexact --quiet
PY="$ROOT/.venv/bin/python"
"$PY" -c "import hava_mcp" 2>/dev/null || {
  echo "error: $PY cannot import hava_mcp after 'uv sync'; fix the venv and retry" >&2
  exit 1
}

# --- 1. server payload, shared by both artifacts -------------------------- #
say "Staging server source"
SERVER="$WORK/server"
mkdir -p "$SERVER"
rsync -a --exclude '__pycache__' --exclude '*.pyc' src "$SERVER/"
cp pyproject.toml uv.lock README.md LICENSE "$SERVER/"
cp packaging/launch.mjs "$SERVER/launch.mjs"
node --check "$SERVER/launch.mjs"

# --- 2. refresh the generated tools reference ----------------------------- #
say "Regenerating skills/hava-diagrams/references/tools.md"
"$PY" scripts/gen_tools_reference.py

# --- 3. the live tool list, for the .mcpb manifest ------------------------ #
say "Checking the skill against the live tools"
"$PY" scripts/check_skill.py

say "Probing the server for its tool list"
TOOLS_JSON="$WORK/tools.json"
( cd "$WORK" && env -u HAVA_API_TOKEN "$PY" "$ROOT/scripts/probe_tools.py" ) > "$TOOLS_JSON"
"$PY" -c "
import json,sys
n=len(json.load(open('$TOOLS_JSON')))
print(f'    {n} tools')
sys.exit(0 if n else 1)"

smoke_archive() {
  local archive="$1" dir="$2"
  "$PY" - "$archive" "$dir" <<'PYEOF'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    archive.extractall(sys.argv[2])
PYEOF
  # Import like Electron's UtilityProcess (not a main-module invocation),
  # from the packed payload and a fresh, isolated, locked environment.
  HAVA_MCP_HOME="$WORK/runtime" "$PY" scripts/probe_tools.py \
    --timeout 120 --discover-first -- node --input-type=module \
    -e 'await import(process.argv[1])' "$dir/server/launch.mjs" > "$dir/probe-tools.json"
  "$PY" - "$TOOLS_JSON" "$dir/probe-tools.json" <<'PYEOF'
import json, sys
assert json.load(open(sys.argv[1])) == json.load(open(sys.argv[2])), "Packed tools differ from source"
PYEOF
  echo "    packed stdio discovery fallback, initialize, tools/list and tools/call OK"
}

build_mcpb() {
  say "Building .mcpb"
  local dir="$WORK/mcpb"
  mkdir -p "$dir"
  rsync -a "$SERVER" "$dir/"
  cp packaging/mcpb/icon.png packaging/mcpb/.mcpbignore "$dir/"
  cp packaging/BUILD_NOTES.md "$dir/"
  "$PY" - "$ROOT/packaging/mcpb/manifest.json" "$TOOLS_JSON" "$VERSION" "$dir/manifest.json" <<'PYEOF'
import json, sys
src, tools_path, version, dest = sys.argv[1:5]
m = json.load(open(src))
m["version"] = version
m["tools"] = json.load(open(tools_path))
m["tools_generated"] = False
json.dump(m, open(dest, "w"), indent=2)
open(dest, "a").write("\n")
PYEOF
  npx -y @anthropic-ai/mcpb@latest validate "$dir/manifest.json" >/dev/null
  echo "    manifest valid"
  # Never pack over a previous same-version build: stale entries would survive.
  rm -f "$OUT_DIR/hava-mcp-$VERSION.mcpb"
  npx -y @anthropic-ai/mcpb@latest pack "$dir" "$OUT_DIR/hava-mcp-$VERSION.mcpb" >/dev/null
  smoke_archive "$OUT_DIR/hava-mcp-$VERSION.mcpb" "$WORK/verify-mcpb"
  echo "    $OUT_DIR/hava-mcp-$VERSION.mcpb"
}

build_plugin() {
  say "Building plugin zip"
  local dir="$WORK/plugin"
  mkdir -p "$dir/.claude-plugin"
  rsync -a "$SERVER" "$dir/"
  cp packaging/plugin/.claude-plugin/plugin.json "$dir/.claude-plugin/plugin.json"
  cp packaging/plugin/mcp.json "$dir/.mcp.json"          # dot-less in the repo
  cp packaging/plugin/README.md "$dir/README.md"
  cp packaging/BUILD_NOTES.md LICENSE "$dir/"
  rsync -a --exclude '__pycache__' skills "$dir/"
  "$PY" - "$dir/.claude-plugin/plugin.json" "$VERSION" <<'PYEOF'
import json, sys
path, version = sys.argv[1:3]
d = json.load(open(path))
d["version"] = version
json.dump(d, open(path, "w"), indent=2)
open(path, "a").write("\n")
PYEOF
  if command -v claude >/dev/null; then
    claude plugin validate --strict "$dir/.claude-plugin/plugin.json" >/dev/null \
      && echo "    plugin manifest valid"
  fi
  # zip updates an existing archive in place, so files deleted since a
  # previous same-version build would otherwise stay in the shipped zip.
  rm -f "$OUT_DIR/cloudzone-hava-$VERSION.zip"
  ( cd "$dir" && zip -qr "$OUT_DIR/cloudzone-hava-$VERSION.zip" . \
      -x '*.DS_Store' -x '*__pycache__*' -x '*.pyc' )
  smoke_archive "$OUT_DIR/cloudzone-hava-$VERSION.zip" "$WORK/verify-plugin"
  echo "    $OUT_DIR/cloudzone-hava-$VERSION.zip"
}

smoke_codex() {
  local archive="$1" dir="$2"
  "$PY" - "$archive" "$dir" <<'PYEOF'
import sys, zipfile
# extractall drops the mode bits, so restore them from the archive.
with zipfile.ZipFile(sys.argv[1]) as archive:
    for info in archive.infolist():
        path = archive.extract(info, sys.argv[2])
        mode = info.external_attr >> 16
        if mode:
            import os
            os.chmod(path, mode)
PYEOF
  # A clean state and config directory, so neither a real token nor an already
  # prepared environment can mask a problem. --prepare builds in the
  # foreground, which is also how the installer and a human use it.
  # A subshell, so the caller's own values survive.
  (
    export HAVA_MCP_HOME="$dir/.runtime" HAVA_MCP_CONFIG_HOME="$dir/.config"
    sh "$dir/server/launch.sh" --prepare >/dev/null
    echo "    prepared environment from the packed source"
    # Codex spawns a stdio server as an ordinary process, so this is exactly
    # how it starts: `sh <plugin root>/server/launch.sh`.
    "$PY" scripts/probe_tools.py --timeout 300 --discover-first -- \
      sh "$dir/server/launch.sh" > "$dir/probe-tools.json"
  )
  "$PY" - "$TOOLS_JSON" "$dir/probe-tools.json" <<'PYEOF'
import json, sys
assert json.load(open(sys.argv[1])) == json.load(open(sys.argv[2])), "Packed tools differ from source"
PYEOF
  echo "    packed stdio launch, initialize, tools/list and tools/call OK"
}

# Install the packed zip into a throwaway HOME and let the real Codex CLI judge
# it. Worth the 2s: an invalid `policy.authentication` once made Codex reject
# the whole marketplace file, and ChatGPT reported only "Could not add plugin.
# Please try again." - nothing in the package itself looked wrong.
verify_codex_install() {
  local archive="$1" home="$2"
  local pkg="$home/pkg"
  mkdir -p "$pkg"
  ( cd "$pkg" && unzip -qo "$archive" )
  ( cd "$pkg" && HOME="$home" bash ./install.sh --no-prepare >/dev/null )

  "$PY" - "$home" <<'PYEOF'
import json, os, sys

home = sys.argv[1]
catalog = json.load(open(os.path.join(home, ".agents/plugins/marketplace.json")))
entry = next(p for p in catalog["plugins"] if p["name"] == "cloudzone-hava")
# codex-cli deserializes these as enums and rejects the whole file on an
# unknown variant, taking every other plugin in the catalog down with it.
assert entry["policy"]["installation"] in {"AVAILABLE", "INSTALLED_BY_DEFAULT", "NOT_AVAILABLE"}, entry
assert entry["policy"]["authentication"] in {"ON_INSTALL", "ON_USE"}, entry
path = entry["source"]["path"]
assert path.startswith("./"), path          # resolved against the root, $HOME here
root = os.path.join(home, path[2:])
assert os.path.isfile(os.path.join(root, "plugin.json")), root
assert not os.path.exists(os.path.join(root, "install.sh")), "installer copied itself"
print(f"    marketplace entry valid, plugin at ~/{path[2:]}")
PYEOF

  if ! command -v codex >/dev/null; then
    echo "    codex CLI not installed: skipped the live install check"
    return 0
  fi
  # A subshell, so HOME and CODEX_HOME are back to the caller's afterwards.
  # Codex output is discarded, so name the check that failed.
  (
    export HOME="$home" CODEX_HOME="$home/.codex"
    check() {
      local what="$1"; shift
      "$@" || { echo "error: codex check failed: $what" >&2; exit 1; }
    }
    check "marketplace lists local-personal" \
      sh -c 'codex plugin marketplace list 2>/dev/null | grep -q local-personal'
    check "plugin list shows cloudzone-hava" \
      sh -c 'codex plugin list 2>/dev/null | grep -q cloudzone-hava'
    check "codex plugin add cloudzone-hava@local-personal" \
      sh -c 'codex plugin add cloudzone-hava@local-personal >/dev/null 2>&1'
    check "plugin is installed, enabled" \
      sh -c 'codex plugin list 2>/dev/null | grep -q "installed, enabled"'
    check "mcp list shows launch.sh" \
      sh -c 'codex mcp list 2>/dev/null | grep -q "launch.sh"'
  )
  echo "    codex $(codex --version 2>/dev/null | awk '{print $2}'): marketplace loads, plugin installs, server registered"
}

build_codex() {
  say "Building Codex plugin zip"
  local dir="$WORK/codex/cloudzone-hava"
  mkdir -p "$dir/assets" "$dir/skills"
  rsync -a "$SERVER" "$dir/"
  # Codex ships neither Node nor Python; the shell launcher replaces launch.mjs.
  rm -f "$dir/server/launch.mjs"
  cp packaging/codex/launch.sh "$dir/server/launch.sh"
  chmod +x "$dir/server/launch.sh"
  sh -n "$dir/server/launch.sh"
  cp packaging/codex/mcp.json "$dir/mcp.json"
  cp packaging/codex/README.md "$dir/README.md"
  cp packaging/codex/install.sh "$dir/install.sh"
  chmod +x "$dir/install.sh"
  bash -n "$dir/install.sh"
  cp packaging/BUILD_NOTES.md LICENSE "$dir/"
  cp packaging/mcpb/icon.png "$dir/assets/icon.png"
  rsync -a --exclude '__pycache__' --exclude '.DS_Store' skills/hava-diagrams "$dir/skills/"
  rsync -a --exclude '__pycache__' --exclude '.DS_Store' packaging/codex/skills/hava-setup "$dir/skills/"
  "$PY" - "$ROOT/packaging/codex/plugin.json" "$VERSION" "$dir/plugin.json" <<'PYEOF'
import json, sys
src, version, dest = sys.argv[1:4]
manifest = json.load(open(src))
manifest["version"] = version
json.dump(manifest, open(dest, "w"), indent=2)
open(dest, "a").write("\n")
PYEOF
  "$PY" scripts/check_codex_package.py "$dir" --expect-version "$VERSION" --out-dir "$OUT_DIR"
  rm -f "$OUT_DIR/cloudzone-hava-mcp-codex-v$VERSION.zip"   # see build_plugin
  ( cd "$dir" && zip -qr "$OUT_DIR/cloudzone-hava-mcp-codex-v$VERSION.zip" . \
      -x '*.DS_Store' -x '*__pycache__*' -x '*.pyc' )
  smoke_codex "$OUT_DIR/cloudzone-hava-mcp-codex-v$VERSION.zip" "$WORK/verify-codex"
  verify_codex_install "$OUT_DIR/cloudzone-hava-mcp-codex-v$VERSION.zip" "$WORK/codex-home"
  echo "    $OUT_DIR/cloudzone-hava-mcp-codex-v$VERSION.zip"
}

case "$WHAT" in
  mcpb)   build_mcpb ;;
  plugin) build_plugin ;;
  codex)  build_codex ;;
  both|all) build_mcpb; build_plugin; build_codex ;;
  *) echo "usage: scripts/build.sh [all|mcpb|plugin|codex]" >&2; exit 2 ;;
esac

say "Done"
# A single-target build leaves the other patterns unmatched, which must not
# fail the build under `set -o pipefail`.
{ ls -lh "$OUT_DIR"/*-"$VERSION".* "$OUT_DIR"/*-v"$VERSION".* 2>/dev/null || true; } \
  | awk '{print "   ",$5,$9}'
