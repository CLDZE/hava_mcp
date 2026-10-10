#!/usr/bin/env bash
# Build both distributable artifacts from this checkout.
#
#   scripts/build.sh            -> both, copied into the MCPB folder
#   scripts/build.sh plugin     -> just the org plugin zip
#   scripts/build.sh mcpb       -> just the Claude Desktop extension
#   OUT_DIR=/somewhere scripts/build.sh
#
# Output filenames carry the version from pyproject.toml, e.g.
#   cloudzone-hava-0.3.0.zip
#   hava-mcp-0.3.0.mcpb
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

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

say "hava-mcp $VERSION  ->  $OUT_DIR"
mkdir -p "$OUT_DIR"
uv lock --check

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
  ( cd "$dir" && zip -qr "$OUT_DIR/cloudzone-hava-$VERSION.zip" . \
      -x '*.DS_Store' -x '*__pycache__*' -x '*.pyc' )
  smoke_archive "$OUT_DIR/cloudzone-hava-$VERSION.zip" "$WORK/verify-plugin"
  echo "    $OUT_DIR/cloudzone-hava-$VERSION.zip"
}

case "$WHAT" in
  mcpb)   build_mcpb ;;
  plugin) build_plugin ;;
  both)   build_mcpb; build_plugin ;;
  *) echo "usage: scripts/build.sh [both|mcpb|plugin]" >&2; exit 2 ;;
esac

say "Done"
ls -lh "$OUT_DIR"/*-"$VERSION".* 2>/dev/null | awk '{print "   ",$5,$9}'
