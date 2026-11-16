#!/usr/bin/env bash
# Install cloudzone-hava for Codex (CLI, IDE extension, and Codex in the
# ChatGPT desktop app).
#
# Copies this plugin to ~/.codex/plugins/cloudzone-hava and registers it in the
# personal marketplace at ~/.agents/plugins/marketplace.json. Re-run it to
# upgrade in place: Codex loads a cached copy of the plugin, so editing the
# installed folder by hand changes nothing until this script runs again.
#
#   ./install.sh              install/upgrade, then prepare the environment
#   ./install.sh --no-prepare install only (the first Codex launch builds it)
set -euo pipefail

NAME="cloudzone-hava"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.codex/plugins/$NAME"
MARKETPLACE="$HOME/.agents/plugins/marketplace.json"

PREPARE=1
[ "${1:-}" = "--no-prepare" ] && PREPARE=0

mkdir -p "$DEST" "$(dirname "$MARKETPLACE")"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete --exclude install.sh "$HERE"/ "$DEST"/
else
  rm -rf "$DEST"
  mkdir -p "$DEST"
  cp -R "$HERE"/. "$DEST"/
  rm -f "$DEST/install.sh"
fi
chmod +x "$DEST/server/launch.sh"

# The marketplace is a JSON catalog. Codex resolves source.path relative to the
# marketplace root, which is $HOME for the personal file - hence the
# ./.codex/plugins/<name> path. Any other plugins already listed are kept.
python3 - "$MARKETPLACE" "$NAME" <<'PY'
import json, os, sys

path, name = sys.argv[1], sys.argv[2]
try:
    with open(path) as handle:
        catalog = json.load(handle)
except Exception:
    catalog = {}
catalog.setdefault("name", "local-personal")
catalog.setdefault("interface", {}).setdefault("displayName", "Personal plugins")
entry = {
    "name": name,
    "source": {"source": "local", "path": f"./.codex/plugins/{name}"},
    # codex-cli accepts ON_INSTALL or ON_USE here, and nothing else: an
    # unknown variant makes Codex reject the whole marketplace file, which
    # ChatGPT reports only as "Could not add plugin. Please try again."
    # This server authenticates when a tool runs, not at install time.
    "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"},
    "category": "Productivity",
}
plugins = catalog.setdefault("plugins", [])
plugins[:] = [p for p in plugins if p.get("name") != name] + [entry]
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as handle:
    json.dump(catalog, handle, indent=2)
    handle.write("\n")
print(f"marketplace: {path}")
PY

VERSION="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$DEST/plugin.json")"
echo "installed $NAME $VERSION -> $DEST"

if [ "$PREPARE" = 1 ]; then
  echo
  echo "Test-building the Python environment (needs uv)..."
  # Codex points HAVA_MCP_HOME at ${PLUGIN_DATA}/runtime, an opaque
  # ~/.codex/plugins/data/agent-plugins/<hash> directory that exists only once
  # the plugin is installed in Codex. So this builds the default state
  # directory: it proves uv and the lockfile work and warms uv's cache, and
  # Codex's first launch then builds its own copy from that cache.
  sh "$DEST/server/launch.sh" --prepare || {
    echo "The environment is not ready yet - see the error above." >&2
    echo "Install uv (brew install uv) and re-run: sh '$DEST/server/launch.sh' --prepare" >&2
  }
fi

cat <<EOF

Next:
  1. Put your Hava API token where the server can read it:

       mkdir -p ~/.hava-mcp
       printf '%s' 'PASTE_THE_TOKEN' > ~/.hava-mcp/api_token
       chmod 600 ~/.hava-mcp/api_token

     (from app.hava.io -> avatar -> Personal Settings -> API Tokens ->
     Display Token. The file is the route that works: Codex does not forward
     your shell environment to a plugin's bundled server.)

  2. Restart Codex, and the ChatGPT desktop app if you use Codex there.
  3. In Codex CLI run /plugins, pick "Personal plugins", install $NAME.
  4. In a new session, call hava_auth_status to confirm, and /skills to see
     hava-diagrams and hava-setup listed.

Checks:  codex plugin marketplace list    # should resolve to $HOME
         sh "$DEST/server/launch.sh" --prepare
EOF
