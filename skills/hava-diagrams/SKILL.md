---
name: hava-diagrams
version: 2
description: Find, inspect, export and manage Hava.io cloud architecture diagrams for AWS, Azure, GCP and Kubernetes accounts. Use whenever someone asks for a cloud architecture diagram, a network/VPC topology picture, a security-group view, an inventory of cloud resources, an "architecture doc" or a Visio/PNG/PDF export of infrastructure — and also when they ask what changed in an environment over time, want to share or embed a diagram, want to refresh diagrams after a deploy, or need to manage Hava teams, projects, members or API tokens. Do NOT use for drawing diagrams by hand (use a diagramming tool) or for live cloud API queries (use the cloud provider's own CLI).
---

# Hava cloud diagrams

Hava continuously imports connected cloud accounts and generates architecture
diagrams from the real configuration. Nothing here changes cloud
infrastructure, but several tools do change things inside Hava.

Full parameter lists for all 44 tools: `references/tools.md`.

## The one thing to understand first

Hava's UI says *diagram*; the API says **environment**, and each environment
holds several **views**:

| view | what it shows | availability |
|---|---|---|
| `infrastructure` | the clean topology diagram | always |
| `extended` | topology plus extra detail | always |
| `security` | security groups and open ports | AWS and Azure only |
| `container` | containerised workloads | only where containers were found |
| `list` | every resource, visualised or not | always |

**Exports act on a view, not on a diagram.** The normal flow is: find the
diagram → pick a view → export. Every listing already returns each view's
`view_id`, so you rarely need an extra lookup.

## Choosing a tool

| the ask | tool |
|---|---|
| "what diagrams do we have?" | `hava_list_diagrams` |
| "find the prod diagram" | `hava_find_diagrams` with `name_contains` |
| "which diagram has vpc-123?" | `hava_find_diagrams` with `resource_query` (see caveats) |
| "what's in this diagram?" | `hava_get_diagram`, `include_resources` if needed |
| "what's running in eu-west-1?" | `hava_list_resources` |
| "dump our whole inventory" | `hava_export_resources` with `save_to` |
| "what changed last week?" | `hava_list_diagram_versions`, then pass `revision_id` |
| "export it as PNG/PDF/Visio" | `hava_export_diagram` |
| "give me a shareable link" | `hava_create_diagram_share` — **publishes publicly** |
| "refresh after our deploy" | `hava_sync_source` |
| "which cloud accounts are connected?" | `hava_list_sources`, `hava_get_source` |
| "make a diagram of just these VPCs" | `hava_create_diagram` with a query |
| "who has access?" | `hava_list_teams`, `hava_list_projects` |
| "audit our API tokens" | `hava_list_api_tokens` |
| auth trouble | `hava_auth_status` |

Every tool takes `response_format`: `markdown` (default, compact) or `json`
when you need to pick fields out programmatically.

## Export format and view compatibility

Validated before calling Hava, so a bad pair fails instantly with a message
naming what *would* work:

| format | views it accepts |
|---|---|
| `png`, `pdf` | infrastructure, extended, security, container — **not** list |
| `vsdx` (editable Visio) | infrastructure **only** |
| `csv`, `json` | every view |

Use `vsdx` when someone wants to edit the diagram, `pdf` for a document, `png`
to paste into a page, and `csv`/`json` when they want resource data rather than
a picture.

## Exports are asynchronous — budget for it

`hava_export_diagram` queues a job and polls it. `wait_seconds` defaults to
100, deliberately under the 120s tool timeout most clients enforce. Large
diagrams genuinely exceed that: a 461-resource VPC took over two minutes as
`vsdx`. When the budget runs out you get a `job_id` back — **that is not a
failure**. Call `hava_get_job` with it, optionally with its own `save_to` and a
larger `wait_seconds`.

Download URLs are short-lived signed CDN links. If the user wants to keep the
file, pass `save_to` rather than handing them a URL that will expire.

## Search query syntax (tested, not as documented)

Fields that **work** on `hava_list_resources` and `hava_find_diagrams`:
`source:<source_id>`, `region:<region>`, `vpc:<vpc-id>`, `cluster:"<arn>"`, and
tag filters such as `CostCenter:DEV`, combined with `AND`/`OR` and parentheses.

- **`type:` does not work**, despite appearing in Hava's docs — it returns zero
  results. Filter by type with `hava_list_resources`'s `type_contains`
  parameter instead.
- **`name:` does not work** either.
- Bare words (no `field:`) are rejected with a 422.

## Context discipline

- A broad resource query matches enormous sets — `region:eu-west-1` returned
  **13,979** resources on a real account. `hava_list_resources` leads with a
  per-type breakdown and caps the listing at `limit`; keep it small and read
  the breakdown rather than paging through everything.
- `hava_get_diagram` omits resources by default. Only pass `include_resources`
  when the resources are the point, and keep `resource_limit` modest.
- `hava_export_resources` is a bulk dump for a **file**. Always pass `save_to`.
- Prefer `hava_find_diagrams` over listing every page when the user named
  something.

## Confirm before these

Tools that change Hava require an explicit `confirm=true` and should never be
called speculatively. Say what will be affected, in the user's own terms, and
wait for a clear yes:

- `hava_delete_source` — **the most destructive call here.** Removes the cloud
  source *and every diagram and version history derived from it*. The cloud
  account itself is untouched.
- `hava_delete_diagram` — permanent, takes the version history with it.
- `hava_delete_team` — per Hava's docs this also removes the team's members
  **from the account**, so it can revoke people's access entirely.
- `hava_delete_api_token` — instant and irreversible; may be the token this
  server itself is using, which would break every later call.
- `hava_delete_project`, `hava_remove_team_member`,
  `hava_remove_project_member`, `hava_delete_diagram_note`.

Also treat these as side effects worth flagging first, even though they are not
destructive:

- `hava_create_diagram_share` **publishes the diagram publicly.** Anyone with
  the URL can view the architecture without signing in — for production that
  means exposing network layout, security groups and resource names.
- `hava_invite_team_member` **emails a real person** and grants them access to
  your architecture documentation.
- `hava_sync_source` calls out to the cloud provider, can take many minutes,
  and adds a new version to every diagram in that source.
- `hava_create_team` / `hava_update_team` with `roles` grants account-wide
  permissions; `roles` **replaces** the list rather than adding to it.
- Writing files: only pass `save_to` when a file was asked for, and say where
  it landed.

## Not available through this server, by design

- **Connecting a cloud source** (`POST /sources`) and rotating its credentials.
  Both need long-lived cloud secrets — AWS secret keys, Azure client secrets,
  GCP service-account JSON — which must not pass through a conversation
  transcript. Do it in the Hava UI. `hava_rename_source` covers the safe part.
- **Minting an API token** (`POST /accounts/{id}/tokens`), because the response
  contains the secret. Create tokens in the Hava UI; listing and revoking them
  is available here for hygiene.

If the user asks for either, say why and point them at the Hava UI.

## Known API caveats

- **`resource_query` on `hava_find_diagrams` is unreliable.** Hava's
  `/search/environments` returns a 500 for some well-formed queries. Fall back
  to `name_contains` — do not keep retrying.
- **Reports need a higher plan.** `/reports` returned 403 on the account this
  was built against; the report tools say so rather than looking broken.
- **A `pending` diagram has no usable views yet** — it is still building.
- **Missing views are normal.** Asking for a view a diagram lacks returns an
  error listing the views it does have; read that instead of guessing again.
- **`page_size` floors at 10** on diagram listings.
- **Renaming a custom diagram does not work.** `hava_update_diagram` accepts
  `name`, Hava returns 200, and the name never changes. The tool re-reads and
  says so. Tell the user to rename in the Hava UI; the query change does apply.
- **A new diagram passes through an undocumented `importing` state.** It has no
  views and cannot be annotated or exported until it reaches `active`;
  `hava_create_diagram` waits for that.

## Authentication

**The only supported way to configure or update the plugin's Hava API token
is from Claude Code running in a terminal, using
`/plugin configure cloudzone-hava@synced`.** Always direct users to this
exact flow when the token is missing, expired, revoked, or needs replacing.

### Configure the token

Get the token from app.hava.io → avatar → **Personal Settings** → **API
Tokens** → **Display Token**. Walk the user through these steps:

1. Open **Terminal** (or iTerm) on their Mac.
2. Run `claude` to start Claude Code.
3. Inside that Claude Code session, enter:

   ```text
   /plugin configure cloudzone-hava@synced
   ```

4. Paste the token into the **Hava API token** field and save.
5. Restart Claude or open a new session, then use `hava_auth_status` to
   verify authentication.

The slash command belongs **inside Claude Code**, after starting `claude`;
it must not be entered directly at the shell prompt. The user enters their
credential in the configuration field, never in the conversation.

Do not suggest any alternative token setup: chat slash commands, plugin
cards or menus, environment variables, config files, or server tools.
If asked for another method, explain that the terminal flow above is the
only supported configuration method.

### Missing tools or failed authentication

Installing the plugin does not configure its token. If this skill is
available but no `hava_*` tools appear, guide the user through the terminal
steps above before retrying tools or suggesting a reinstall.

If tools remain missing after configuration, have the user run
`claude plugin list` in their terminal and check that
`cloudzone-hava@synced` is loaded. A duplicate Hava installation may shadow
the synced plugin; resolve that duplicate, then repeat
`/plugin configure cloudzone-hava@synced` inside Claude Code. Do not switch
to configuring a different plugin ID or another token setup method.

If `hava_auth_status` reports a rejected token, have the user obtain a
valid token from Hava and replace it through the same terminal flow.

API access needs a Hava Teams/Business plan or a self-hosted install.
Read-only mode is an option in the same configuration flow and hides every
mutating tool.

`hava_delete_api_token` revokes a Hava credential; it does not configure
the token used by this plugin.
