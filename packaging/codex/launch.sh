#!/bin/sh
# Launcher for the Hava MCP server inside an OpenAI Codex plugin.
#
# Why a shell script rather than the Node launcher the Claude packages use:
#
#   - Codex ships neither Node nor Python, but every host that runs Codex has
#     a POSIX shell. `uv` is the one prerequisite, exactly as before.
#   - Codex spawns a stdio MCP server as an ordinary child process, so fd 0/1
#     *are* the MCP stream. There is nothing to bridge - we exec the server
#     and get out of the way. (Claude Desktop runs an .mcpb inside an Electron
#     UtilityProcess, where that is not true; hence launch.mjs.)
#
# Why a prepared virtualenv instead of `uv run` on every launch: Codex kills a
# server that has not answered tools/list within startup_timeout_sec, 10s by
# default. `uv sync` is 0.2s warm but 3-5s cold and slower still on a first
# run that has to download wheels. So the environment is built once into the
# plugin's data directory and later launches exec that interpreter directly.
# The build is detached and locked, so a launch that Codex gives up on still
# finishes its build and the next one is on the fast path.
#
# Configuration: Codex has no install-time prompt and a plugin's bundled MCP
# server cannot be given env values after installation, so this script - not
# the host - resolves the token, from the environment or from a 0600 file.
#
#   HAVA_API_TOKEN           the token (wins over the file, but note that
#                            Codex forwards only its own env allowlist, a
#                            literal `env` and a config.toml server's
#                            `env_vars` - so for a plugin-bundled server the
#                            file is normally the only route)
#   HAVA_API_TOKEN_FILE      a file holding it, read instead of the default
#   HAVA_MCP_CONFIG_HOME     where api_token/endpoint/read_only live
#                            (default ~/.hava-mcp)
#   HAVA_MCP_HOME            where the prepared virtualenv lives
#                            (mcp.json points this at ${PLUGIN_DATA}/runtime)
#   HAVA_MCP_UV              full path to uv, if it is somewhere unusual
#
# Usage:
#   launch.sh              run the MCP server over stdio (what Codex does)
#   launch.sh --prepare    build the environment and exit; no server, no token
#
set -u

SERVER_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
log() { printf '[hava-mcp] %s\n' "$*" >&2; }

PREPARE=0
case "${1:-}" in
  --prepare) PREPARE=1 ;;
  "") ;;
  *) log "FATAL: unknown argument '$1' (expected --prepare or nothing)"; exit 2 ;;
esac

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Trim surrounding whitespace and reject a placeholder a host left unexpanded,
# so "${user_config.api_token}" never reaches the API as a literal token.
clean() {
  value=$(printf '%s' "${1:-}" | tr -d '\000' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  case "$value" in
    '${'*'}') return 0 ;;
    *) printf '%s' "$value" ;;
  esac
}

read_clean_file() {
  [ -f "$1" ] && [ -r "$1" ] || return 0
  clean "$(cat -- "$1" 2>/dev/null)"
}

CONFIG_HOME=$(clean "${HAVA_MCP_CONFIG_HOME:-}")
[ -n "$CONFIG_HOME" ] || CONFIG_HOME="$HOME/.hava-mcp"

TOKEN=$(clean "${HAVA_API_TOKEN:-}")
if [ -z "$TOKEN" ]; then
  TOKEN_FILE=$(clean "${HAVA_API_TOKEN_FILE:-}")
  [ -n "$TOKEN_FILE" ] || TOKEN_FILE="$CONFIG_HOME/api_token"
  TOKEN=$(read_clean_file "$TOKEN_FILE")
  [ -n "$TOKEN" ] && TOKEN_ORIGIN="$TOKEN_FILE" || TOKEN_ORIGIN=""
else
  TOKEN_ORIGIN="\$HAVA_API_TOKEN"
fi

ENDPOINT=$(clean "${HAVA_ENDPOINT:-}")
[ -n "$ENDPOINT" ] || ENDPOINT=$(read_clean_file "$CONFIG_HOME/endpoint")

READ_ONLY=0
case "$(clean "${HAVA_MCP_READ_ONLY:-}" | tr '[:upper:]' '[:lower:]')" in
  1 | true | yes | on) READ_ONLY=1 ;;
esac
[ -e "$CONFIG_HOME/read_only" ] && READ_ONLY=1

# ---------------------------------------------------------------------------
# uv
# ---------------------------------------------------------------------------

find_uv() {
  override=$(clean "${HAVA_MCP_UV:-}")
  if [ -n "$override" ] && [ -x "$override" ]; then
    printf '%s' "$override"
    return 0
  fi
  if found=$(command -v uv 2>/dev/null) && [ -x "$found" ]; then
    printf '%s' "$found"
    return 0
  fi
  # A GUI-launched Codex host need not have a shell PATH.
  for candidate in \
    /opt/homebrew/bin/uv \
    /usr/local/bin/uv \
    "$HOME/.local/bin/uv" \
    "$HOME/.cargo/bin/uv" \
    /opt/local/bin/uv; do
    [ -x "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  done
  return 1
}

if ! UV=$(find_uv); then
  log "FATAL: could not find \`uv\`, which is required to run this Python server."
  log "Install it, then start a new Codex session:"
  log "  brew install uv"
  log "  (or) curl -LsSf https://astral.sh/uv/install.sh | sh"
  log "Already installed somewhere unusual? Set HAVA_MCP_UV to its full path."
  exit 1
fi

# ---------------------------------------------------------------------------
# The prepared environment
# ---------------------------------------------------------------------------

state_dir() {
  override=$(clean "${HAVA_MCP_HOME:-}")
  if [ -n "$override" ]; then
    printf '%s' "$override"
  elif [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then
    printf '%s' "$HOME/Library/Application Support/hava-mcp-codex"
  else
    printf '%s' "${XDG_CACHE_HOME:-$HOME/.cache}/hava-mcp-codex"
  fi
}

sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum
  else
    return 1
  fi
}

# Identity of the code about to run: the locked dependencies plus every source
# file. A new plugin version - or an edited working copy - changes the stamp
# and so rebuilds, rather than relying on someone bumping a version string.
source_stamp() {
  {
    printf 'hava-mcp-codex-env-v1\n'
    cat -- "$SERVER_DIR/pyproject.toml" "$SERVER_DIR/uv.lock" || return 1
    find "$SERVER_DIR/src" -type f ! -name '*.pyc' ! -path '*/__pycache__/*' \
      | LC_ALL=C sort \
      | while IFS= read -r file; do
          printf '%s\n' "${file#"$SERVER_DIR"}"
          cat -- "$file" || exit 1
        done
  } | sha256 | cut -c1-16
}

STATE=$(state_dir)
if ! STAMP=$(source_stamp) || [ -z "$STAMP" ]; then
  log "FATAL: cannot fingerprint the bundled source and lockfile under $SERVER_DIR"
  exit 1
fi

ENV_DIR="$STATE/venv-$STAMP"
ENV_PYTHON="$ENV_DIR/bin/python"
ENV_READY="$ENV_DIR/.ready"
ENV_FAILED="$ENV_DIR.failed"
LOCK_DIR="$STATE/.building-$STAMP"
BUILD_LOG="$STATE/build-$STAMP.log"
# A build killed with SIGKILL skips its cleanup trap and leaves the lock
# behind. The builder records its PID in the lock, so a lock whose builder is
# gone is cleared however young it is, and one whose builder is alive is kept
# for up to LOCK_MAX_MINUTES, far longer than a cold first build (wheels, even
# Python itself) takes; the cap only guards against a recycled PID. The short
# age is the fallback for a lock that died before it recorded a PID.
LOCK_STALE_MINUTES=2
LOCK_MAX_MINUTES=30

env_ready() { [ -f "$ENV_READY" ] && [ -x "$ENV_PYTHON" ]; }

# True when the lock was last modified more than $1 minutes ago. find rather
# than stat: BSD and GNU stat share no flag for a modification time.
lock_older_than() {
  [ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +"$1" 2>/dev/null)" ]
}

# kill -0 also fails (EPERM) for a live process we may not signal - another
# user's, or one outside a sandbox - so ask ps before calling it gone.
pid_alive() {
  kill -0 "$1" 2>/dev/null || ps -p "$1" >/dev/null 2>&1
}

lock_stale() {
  [ -d "$LOCK_DIR" ] || return 1
  owner=$(cat -- "$LOCK_DIR/pid" 2>/dev/null) || owner=""
  case "$owner" in
    '' | *[!0-9]*) lock_older_than "$LOCK_STALE_MINUTES" ;;
    *) ! pid_alive "$owner" || lock_older_than "$LOCK_MAX_MINUTES" ;;
  esac
}

start_build() {
  if ! mkdir -p -- "$STATE" 2>/dev/null; then
    log "cannot create $STATE"
    return 1
  fi

  # Rename before deleting: when two launchers find the same lock stale, only
  # one rename succeeds, and the other leaves alone whatever lock exists by
  # then instead of deleting it. mkdir below still decides who builds.
  if lock_stale && mv -- "$LOCK_DIR" "$LOCK_DIR.stale.$$" 2>/dev/null; then
    log "cleared a stale build lock (builder gone)"
    rm -rf -- "$LOCK_DIR.stale.$$"
  fi

  # mkdir is atomic, so it doubles as the lock against a second launcher
  # building the same directory underneath this one.
  if ! mkdir -- "$LOCK_DIR" 2>/dev/null; then
    log "another launcher is already building the environment; waiting for it"
    return 0
  fi
  rm -f -- "$ENV_FAILED"

  log "preparing the Python environment in $ENV_DIR (first launch only)"

  # The build installs packages; it has no business holding the API token.
  build='
    set -e
    trap '"'"'rc=$?; [ "$rc" -eq 0 ] || : > "$FAILED"; if [ "$(cat "$LOCK/pid" 2>/dev/null)" = "$$" ]; then rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null || true; fi'"'"' EXIT
    echo "$$" > "$LOCK/pid"
    rm -rf -- "$ENVDIR"
    "$UV" sync --project "$SRC" --locked --no-dev --no-editable --python ">=3.10"
    "$ENVDIR/bin/python" -I -c "import hava_mcp.server"
    : > "$ENVDIR/.ready"
  '

  if [ "$PREPARE" = 1 ]; then
    # Nobody is waiting on a deadline here, and a human (or an installer, or
    # the package build) wants to see what goes wrong: run it in the
    # foreground, with uv's progress on stderr where it belongs.
    HAVA_API_TOKEN= UV_PROJECT_ENVIRONMENT="$ENV_DIR" \
      /bin/sh -c "$build" 2>&1 | tee -a "$BUILD_LOG" >&2
    return 0
  fi

  # Detached on purpose: when Codex's startup deadline passes it kills this
  # launcher, and a build that died with it would restart from nothing next
  # time and never finish. The build outlives us, so the next launch is fast.
  HAVA_API_TOKEN= UV_PROJECT_ENVIRONMENT="$ENV_DIR" \
    nohup /bin/sh -c "$build" >>"$BUILD_LOG" 2>&1 &

  log "build log: $BUILD_LOG"
  return 0
}

# Give a build in progress a chance to finish, stopping early if it fails.
wait_for_env() {
  deadline=$(($(date +%s) + $1))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    env_ready && return 0
    [ -f "$ENV_FAILED" ] && return 1
    sleep 1
  done
  env_ready
}

export ENVDIR="$ENV_DIR" SRC="$SERVER_DIR" LOCK="$LOCK_DIR" FAILED="$ENV_FAILED" UV="$UV"

if ! env_ready; then
  start_build || exit 1
  if ! wait_for_env 300 || ! env_ready; then
    log "FATAL: the locked environment is not ready."
    [ -f "$BUILD_LOG" ] && tail -n 20 -- "$BUILD_LOG" >&2
    log "Fix the error above, then run:  sh '$SERVER_DIR/launch.sh' --prepare"
    exit 1
  fi
fi

if [ "$PREPARE" = 1 ]; then
  log "environment ready: $ENV_PYTHON"
  exit 0
fi

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

log "uv: $UV"
if [ -n "$TOKEN" ]; then
  log "token: configured (from $TOKEN_ORIGIN)"
else
  log "token: NOT CONFIGURED - write it to $CONFIG_HOME/api_token"
fi
log "endpoint: ${ENDPOINT:-https://api.hava.io (default)}"
log "read-only: $([ "$READ_ONLY" = 1 ] && echo yes || echo no)"

# Pass configuration through if it is set, and remove the variable entirely if
# it is not, so the server reports "not configured" rather than authenticating
# with an empty string.
if [ -n "$TOKEN" ]; then export HAVA_API_TOKEN="$TOKEN"; else unset HAVA_API_TOKEN; fi
if [ -n "$ENDPOINT" ]; then export HAVA_ENDPOINT="$ENDPOINT"; else unset HAVA_ENDPOINT; fi
if [ "$READ_ONLY" = 1 ]; then export HAVA_MCP_READ_ONLY=1; else unset HAVA_MCP_READ_ONLY; fi

# Tells the server which host it is under, so "no token configured" explains
# the Codex route rather than Claude's install-time prompt.
export HAVA_MCP_HOST=codex

unset ENVDIR SRC LOCK FAILED UV
exec "$ENV_PYTHON" -I -m hava_mcp
