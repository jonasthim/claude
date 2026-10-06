# authentik: a Claude Code plugin for authentik

A [Claude Code](https://claude.com/claude-code) plugin for operating an
[authentik](https://goauthentik.io) identity provider. It ships one skill, **authentik**, that
teaches Claude to:

- find out why a login is refused ("Bob gets access denied on Dashboards"): authentication or
  authorization, which binding, which group, which event
- report on the server: outposts that are disconnected or outdated, providers no outpost serves,
  applications every user can open
- add an application with its provider and bindings in one atomic call
- change who may open an application, with the effect of the policy engine mode spelled out
- audit access: superusers, unused accounts, tokens, recent configuration changes

It talks to the **authentik REST API** (`/api/v3`) with an API token. No third-party
dependencies: the bundled CLI is stdlib-only Python 3.

Every change follows a plan → confirm → apply → verify protocol, and the CLI itself refuses to
send a mutating request unless `--yes` is passed, so Claude cannot change your identity provider
by accident.

## Requirements

- An authentik server. Written against 2026.8.
- Claude Code on a machine that can reach it.
- `python3` (3.8+).

## Install

Install from the [`jonasthim` marketplace](../../README.md#quick-start):

```
claude plugin marketplace add jonasthim/claude
claude plugin install authentik@jonasthim
```

For local development, load it from a checkout for one session:

```
claude --plugin-dir <checkout>/plugins/authentik
```

The CLI also runs directly from a checkout without installing the plugin:
`python3 <checkout>/plugins/authentik/skills/authentik/scripts/authentik.py info`.

## Configure

1. **Create a service account** in the admin interface: *Directory → Users → Create service
   account*. The password it shows is an app password and is not what this plugin uses.
2. **Decide what it may do** by the groups you add it to:
   - read-only: the group `authentik Read-only`, which authentik ships. Start here.
   - changes: a group that is a superuser group, such as `authentik Admins`. The plugin's own
     confirmation gate is then the only thing between a mistake and your login system, so give
     this only when you want Claude to make changes.
3. **Create an API token** for that account: *Directory → Tokens and App passwords → Create*,
   intent *API Token*, user the service account, and **Expiring off**. An expiring API token
   lasts a day by default and is then rotated. Copy its key.
4. Export the variables in the shell you start Claude Code from (or put them in a file only you
   can read and source it):

```bash
export AUTHENTIK_HOST=https://auth.example.com
export AUTHENTIK_TOKEN='...'
# export AUTHENTIK_CA_CERT=~/.config/my-ca.pem   # only for a privately signed certificate
```

| Variable | Required | Purpose |
|---|---|---|
| `AUTHENTIK_HOST` | yes | Address of the authentik server; `/api/v3` is added when absent |
| `AUTHENTIK_TOKEN` | yes | An API token (intent `api`) |
| `AUTHENTIK_CA_CERT` | no | CA bundle for a privately signed certificate |
| `AUTHENTIK_VERIFY_TLS` | no | `0` turns certificate verification off. On by default; Claude never turns it off |
| `AUTHENTIK_TIMEOUT` | no | HTTP timeout in seconds, default 20 |

Then, in Claude Code: run `/authentik:doctor`, or ask *"check my authentik setup"*.

## Live checklist (first time)

```bash
A="python3 plugins/authentik/skills/authentik/scripts/authentik.py"
$A info                               # who the token is, server version
$A apps list --table
$A apps bindings <a slug> --table     # the engine mode and what it means
$A providers list --table
$A outposts list --table
$A outposts health <an outpost> --table
$A events list --table --limit 10
$A report health
$A apps patch <a slug> --body '{"meta_description": "test"}' --dry-run   # prints the request, sends nothing
```

## Try it without a server

```bash
export AUTHENTIK_MOCK_DIR=$PWD/plugins/authentik/skills/authentik/evals/fixtures
python3 plugins/authentik/skills/authentik/scripts/authentik.py apps bindings dashboards --table
```

The fixtures describe a synthetic server. Writes are echoed back and never stored.
`skills/authentik/evals/mock_server.py` serves the same fixtures over HTTP.

## What's inside

```
plugins/authentik/
  skills/authentik/SKILL.md             workflow and safety instructions Claude follows
  skills/authentik/scripts/authentik.py     stdlib-only CLI over the REST API
  skills/authentik/references/          API reference and playbooks
  skills/authentik/evals/               test prompts, trigger prompts, fixtures, mock server
  skills/doctor/SKILL.md                /authentik:doctor
  tests/                                offline tests
```

## Verified

The API reference is condensed from the authentik 2026.8.3 OpenAPI schema, source and
documentation. The CLI is tested offline against fixtures and a mock server (`python3 -m unittest
discover tests` from this folder). It has **not yet been run against a live authentik server**;
statements those sources did not settle are marked UNVERIFIED in the references, and the first
live run will replace those marks with what the server actually does.

## Safety model

- The skill pre-approves its read commands and nothing else. That holds when you start from
  `/authentik:authentik <your question>` or `/authentik:doctor` (checked in a non-interactive
  session). When Claude picks the skill up on its own, your permission mode may still ask for a
  read; allow the read commands once if you want them silent.
- Every write prints the exact request and exits with code 3 until `--yes` is given; `--dry-run`
  prints it and never sends. Writes and `raw` are not pre-approved, so your permission mode asks
  as well.
- Claude shows one plan per change and says what it does to who can log in where. It names the
  policy engine mode in every plan that touches a binding, because the same change widens access
  under `any` and narrows it under `all`.
- OAuth2 client secrets, which authentik returns in every provider read, and the other
  credentials the API can return are redacted in all output unless `--show-secrets` is passed.
  The CLI's own token is never printed, even with that flag, although `/admin/system/` echoes it.
- TLS certificates are verified. Redirects are not followed, so the token is never sent to
  another address.
- Claude does not create tokens or read their keys, set passwords, create recovery links or
  impersonate users through this plugin: each would put a working credential for an account into
  the conversation.
- Default flows, stages and brands, and objects authentik marks as `managed`, are left alone
  unless you ask for exactly that change.

## Not covered

Installing and upgrading authentik, writing flows, stages, expression policies and blueprints,
and the client side of an application's SSO setup. Sources, stages, certificates, brands, roles
and RBAC permissions are reachable through `raw` and have no dedicated commands yet.

## License

MIT
