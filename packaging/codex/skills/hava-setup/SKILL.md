---
name: hava-setup
description: Set up or repair the Hava MCP server in Codex — install uv, prepare its Python environment, configure the Hava API token, switch read-only mode or a self-hosted endpoint on. Use when the hava_* tools are missing, when hava_auth_status reports no token configured, when a Hava tool fails with 401/403, or right after installing the cloudzone-hava plugin.
---

# Setting up Hava in Codex

The plugin ships a local Python MCP server. Codex never prompts for
credentials, so three things have to be true before the `hava_*` tools work:
`uv` is installed, the server's environment is built, and a Hava API token is
readable.

**Never ask the user to paste the token into this conversation.** It would be
stored in the transcript. Print the commands and let them run them.

## 1. Find the installed plugin

Codex runs the *cached* copy, not the folder it was installed from:

```bash
ls -d ~/.codex/plugins/cache/*/cloudzone-hava/*/server/launch.sh
```

Use that path as `$LAUNCH` below. Nothing found means the plugin is not
installed — run `install.sh` from the package again, restart Codex, then
install it from the Plugins Directory (`/plugins` in the CLI).

## 2. uv

```bash
command -v uv || brew install uv
```

`uv` supplies Python 3.10+ and the dependencies. Without it the server cannot
start at all, and its log says so. If uv lives somewhere unusual, set
`HAVA_MCP_UV` to its full path.

## 3. Prepare the environment

The first launch builds a virtualenv, which can take longer than the 10s Codex
allows a server to start. Building one now proves uv and the dependencies work
and fills uv's download cache, so Codex's own build (in the plugin's data
directory, which a shell here cannot see) only installs from that cache:

```bash
sh "$LAUNCH" --prepare
```

It prints `environment ready: …` and exits. Re-running it is free once built.

## 4. The API token

Tell the user to get it from <https://app.hava.io/settings/profile> — avatar →
**Personal Settings** → **API Tokens** → **Display Token** — and to run, in
their own terminal:

```bash
mkdir -p ~/.hava-mcp
printf '%s' 'PASTE_THE_TOKEN' > ~/.hava-mcp/api_token
chmod 600 ~/.hava-mcp/api_token
```

The file is the route that works. Codex gives a local MCP server only its own
environment allowlist, a server's literal `env`, and the names a
`config.toml` server lists in `env_vars` — and a plugin's bundled server
cannot declare those. So `export HAVA_API_TOKEN=...` in a shell usually never
reaches this server; don't send the user down that path.

API access requires a Hava Teams/Business plan or a self-hosted install; a
Starter/Personal plan returns 401 whatever the token says.

## 5. Options (all optional, same directory)

| file | effect |
| --- | --- |
| `~/.hava-mcp/read_only` | exists → hide every tool that changes anything in Hava, leaving 19 read-only tools |
| `~/.hava-mcp/endpoint` | a self-hosted Hava base URL instead of `https://api.hava.io` |

`HAVA_MCP_CONFIG_HOME` moves all three files elsewhere.

## 6. Verify

Start a new Codex session (the server is only read at session start), then call
`hava_auth_status` with `verify=true`. It reports the endpoint, where the token
came from and a live API check.

If the tools are still missing:

- `sh "$LAUNCH" --prepare` is the fastest diagnosis — it reports a missing
  `uv`, an unwritable state directory or a failed install, and prints the path
  of its build log (`build-<stamp>.log`, inside the runtime directory Codex
  gives the plugin).
- `/mcp` in the Codex TUI lists the servers that actually started. A plugin
  server can be switched off in `~/.codex/config.toml`:

  ```toml
  [plugins."cloudzone-hava".mcp_servers.hava]
  enabled = true
  ```

- A cold machine can still miss Codex's startup deadline on the very first
  launch. Nothing is broken: the build is detached and keeps going, so a
  second session a minute later starts fast. `--prepare` makes that build
  faster by warming uv's cache, but Codex still builds its own copy once.

Once `hava_auth_status` is OK, hand over to the `hava-diagrams` skill for the
actual work.
