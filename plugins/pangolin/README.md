# pangolin: a Claude Code plugin for Pangolin

A [Claude Code](https://claude.com/claude-code) plugin for operating a self-hosted
[Pangolin](https://github.com/fosrl/pangolin) server, the tunnelled reverse proxy built on Newt,
Gerbil and Traefik. It ships one skill, **pangolin**, that teaches Claude to:

- troubleshoot a published application ("why is app.example.com a bad gateway?") across the
  domain, the resource, the site's tunnel and the target
- report what you publish: offline sites, unhealthy and unmonitored targets, and resources that
  anyone on the internet can reach without a login
- publish a service: resource, access settings, targets and health checks, in that order
- make guarded changes to resources, targets, rules and access settings

It talks to the **Pangolin Integration API** with an API key. No third-party dependencies: the
bundled CLI is stdlib-only Python 3.

Every change follows a plan → confirm → apply → verify protocol, and the CLI itself refuses to
send a mutating request unless `--yes` is passed, so Claude cannot change your proxy by accident.

## Requirements

- A self-hosted Pangolin server with the Integration API enabled. Written against Pangolin
  1.24; the routes used exist from 1.18 on.
- Claude Code on a machine that can reach the API address.
- `python3` (3.8+).

## Install

Install from the [`jonasthim` marketplace](../../README.md#quick-start):

```
claude plugin marketplace add jonasthim/claude
claude plugin install pangolin@jonasthim
```

For local development, load it from a checkout for one session:

```
claude --plugin-dir <checkout>/plugins/pangolin
```

The CLI also runs directly from a checkout without installing the plugin:
`python3 <checkout>/plugins/pangolin/skills/pangolin/scripts/pangolin.py info`.

## Configure

1. **Enable the Integration API** on the server if it is not on yet: `flags.enable_integration_api:
   true` in Pangolin's `config.yml`, and a route to its port (3003 by default), usually
   `api.<your domain>`. The Pangolin documentation has the Traefik snippet. `https://<API
   address>/v1/docs` shows the Swagger page when it works.
2. **Create an API key** in the dashboard: *Organization → API Keys* for one organization, or
   *Server Admin → API Keys* for a root key. Choose its permissions there: only the list and get
   permissions for a read-only plugin, plus the create, update and delete permissions of sites,
   resources, targets and rules when you want Claude to make changes. The key is shown once.
3. Export the variables in the shell you start Claude Code from (or put them in a file only you
   can read and source it):

```bash
export PANGOLIN_HOST=https://api.example.com   # the Integration API address, not the dashboard
export PANGOLIN_API_KEY='<id>.<secret>'        # as shown at creation
export PANGOLIN_ORG=my-org                     # the organization id (the slug in the dashboard address)
# export PANGOLIN_CA_CERT=~/.config/my-ca.pem  # only for a privately signed certificate
```

| Variable | Required | Purpose |
|---|---|---|
| `PANGOLIN_HOST` | yes | Address of the Integration API; `/v1` is added when absent |
| `PANGOLIN_API_KEY` | yes | The API key |
| `PANGOLIN_ORG` | yes for organization keys | Organization id. A root key that sees exactly one organization can omit it |
| `PANGOLIN_CA_CERT` | no | CA bundle for a privately signed certificate |
| `PANGOLIN_VERIFY_TLS` | no | `0` turns certificate verification off. On by default; Claude never turns it off |
| `PANGOLIN_TIMEOUT` | no | HTTP timeout in seconds, default 20 |

Then, in Claude Code: run `/pangolin:doctor`, or ask *"check my pangolin setup"*.

## Live checklist (first time)

```bash
P="python3 plugins/pangolin/skills/pangolin/scripts/pangolin.py"
$P info
$P access --table                 # which reads the key allows
$P sites list --table
$P resources list --table
$P targets list <a resource> --table
$P domains list --table
$P report health --table
$P resources disable <a resource> --dry-run   # prints the request, sends nothing
```

## Try it without a server

```bash
export PANGOLIN_MOCK_DIR=$PWD/plugins/pangolin/skills/pangolin/evals/fixtures
python3 plugins/pangolin/skills/pangolin/scripts/pangolin.py report health --table
```

The fixtures describe a synthetic organization. Writes are echoed back and never stored.
`skills/pangolin/evals/mock_server.py` serves the same fixtures over HTTP.

## What's inside

```
plugins/pangolin/
  skills/pangolin/SKILL.md          workflow and safety instructions Claude follows
  skills/pangolin/scripts/pangolin.py   stdlib-only CLI over the Integration API
  skills/pangolin/references/       API reference and playbooks
  skills/pangolin/evals/            test prompts, trigger prompts, fixtures, mock server
  skills/doctor/SKILL.md            /pangolin:doctor
  tests/                            offline tests
```

## Verified

The API reference is condensed from the Pangolin 1.24.0 source and documentation. The CLI is
tested offline against fixtures and a mock server (`python3 -m unittest discover tests` from this
folder). It has **not yet been run against a live Pangolin server**; statements the source did
not settle are marked UNVERIFIED in the references, and the first live run will replace those
marks with what the server actually does.

## Safety model

- The skill pre-approves its read commands and nothing else. That holds when you start from
  `/pangolin:pangolin <your question>` or `/pangolin:doctor` (checked in a non-interactive
  session). When Claude picks the skill up on its own, your permission mode may still ask for a
  read; allow the read commands once if you want them silent.
- Every write prints the exact request and exits with code 3 until `--yes` is given; `--dry-run`
  prints it and never sends. Writes are not pre-approved, so your permission mode asks as well.
- Claude shows one plan per change and calls out what it does to availability and to protection:
  turning off SSO, clearing a password or emptying the allowed roles can open an application to
  the internet.
- Credentials the API returns (a new site's secret, access tokens, a target's auth token,
  identity-provider client secrets, and every value in health-check and request headers) are
  redacted in all output unless `--show-secrets` is passed. The CLI's own API key is never
  printed, even with that flag.
- TLS certificates are verified. Redirects are not followed, so the key is never sent to another
  address.
- Resource passwords and pincodes are yours to choose and set; Claude does not invent them.

## Not covered

Installing and upgrading the Pangolin server, Traefik and CrowdSec configuration files, the Newt
and Olm clients themselves, and logs: the API has configuration and health state, not request
logs. Private (site) resources, resource policies, clients, blueprints and the AI gateway routes
are reachable through `raw` and have no dedicated commands yet.

## License

MIT
