# Hava Cloud Diagrams for Codex (cloudzone-hava)

A Codex plugin — in the portable [Agent Plugins](https://agent-plugins.org)
format — that connects to [Hava.io](https://www.hava.io) and gives Codex your
automatically generated cloud architecture diagrams. Hava continuously imports
your connected AWS, Azure, GCP and Kubernetes accounts and draws topology,
security and container views from the real configuration.

The plugin bundles three things:

- a local **MCP server** with 47 tools over the Hava API: list and search
  diagrams, query the underlying cloud resources, read dated version history,
  export any view to PNG, PDF, editable Visio (VSDX), CSV or JSON, manage notes
  and embeddable shares, trigger source re-imports, and administer teams,
  projects and API tokens;
- the **`hava-diagrams`** skill, which teaches Codex which tool to reach for,
  which export formats work with which views, how to wait on Hava's
  asynchronous export jobs, and what to confirm before doing;
- the **`hava-setup`** skill, which is the onboarding workflow for the token
  and the Python environment.

Destructive tools require an explicit `confirm=true`. Create
`~/.hava-mcp/read_only` to remove every mutating tool from the list entirely,
leaving 19 read-only tools.

## Where it runs

Codex CLI, the Codex IDE extension and Codex in the ChatGPT desktop app — on
**macOS**, on the machine where the server runs. Because the bundled server is
a local (stdio) one, this package is not eligible for the public plugin
directory; a ChatGPT workspace admin can still publish it to the workspace.

## Requirements

- macOS, and a POSIX shell (the launcher is `sh`; Codex ships no Node or
  Python).
- [`uv`](https://docs.astral.sh/uv) — `brew install uv`. It supplies Python
  3.10+ and the locked dependencies on first launch.
- A Hava API token, and a Hava Teams/Business plan or a self-hosted install.

## Install

There is no file to upload. ChatGPT's Plugins Directory installs from a
*marketplace*, never from a zip, so dragging this archive into ChatGPT →
Plugins fails with "Could not add plugin. Please try again." Run the installer
on the Mac instead:

```bash
./install.sh
```

It copies the plugin to `~/.codex/plugins/cloudzone-hava`, registers it in
`~/.agents/plugins/marketplace.json`, and test-builds the Python environment,
which also fills uv's download cache. Then restart Codex, run `/plugins`, pick
**Personal plugins** and install `cloudzone-hava`. The first Codex start is
still slower than later ones: Codex gives the plugin its own data directory
(`~/.codex/plugins/data/...`), so the server builds its environment once more
there, from the cache. Re-run `install.sh` to upgrade — Codex loads a cached copy,
so a restart is required.

## Configuration

Codex has no install-time prompt, and a plugin's bundled server cannot be given
environment values after installation, so the launcher resolves its own
configuration:

| setting | file | environment |
| --- | --- | --- |
| API token | `~/.hava-mcp/api_token` (chmod 600) | `HAVA_API_TOKEN` |
| self-hosted endpoint | `~/.hava-mcp/endpoint` | `HAVA_ENDPOINT` |
| read-only mode | `~/.hava-mcp/read_only` (any content) | `HAVA_MCP_READ_ONLY=1` |

**Use the files.** An environment variable wins where it arrives, but Codex
passes a local MCP server only its own allowlist, a server's literal `env` and
the names a `config.toml` server lists in `env_vars`, and a plugin's bundled
server cannot declare any of those — so a value exported in your shell
normally never reaches it.

`HAVA_MCP_CONFIG_HOME` moves that directory; `HAVA_MCP_UV` points at `uv` if
it is somewhere unusual. Nothing is written back: the server stores no
credentials and the launcher only reads them.

## Data

Tools call `https://api.hava.io` (or your self-hosted endpoint) with your API
token, and return diagram, resource, export and account data into the
conversation. Exports are downloaded to the path you ask for. Hava's privacy
policy covers the service: <https://www.hava.io/privacy>.
`hava_update_source_credentials` and `hava_create_api_token` deliberately put
cloud credentials or a new token into the transcript — the skill warns before
using them.

## Troubleshooting

- **No `hava_*` tools.** Run the `hava-setup` skill, or
  `sh ~/.codex/plugins/cache/*/cloudzone-hava/*/server/launch.sh --prepare`.
  `/mcp` in the TUI lists the servers that started.
- **First launch times out.** The environment build is detached and keeps
  running after Codex gives up, so the next session starts fast. `--prepare`
  run by hand builds a separate copy outside Codex's plugin data directory, so
  it only makes that first build faster (warm uv cache); it cannot skip it.
- **401/403.** `hava_auth_status verify=true` reports where the token came
  from; API access needs a Hava Teams/Business plan.
