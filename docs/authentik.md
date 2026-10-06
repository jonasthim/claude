# authentik

Operates an [authentik](https://goauthentik.io) identity provider through its REST API: why a login is
refused, applications and their providers, groups and policy bindings, outposts, the event log, and access
audits. The bundled CLI (`authentik.py`) is standard-library Python 3.8+.

Needs an authentik server reachable from where Claude Code runs. Written against authentik 2026.8 from its
OpenAPI schema, source and documentation; it has not been run against a live server yet.

Full reference: [plugins/authentik/README.md](https://github.com/jonasthim/claude/blob/main/plugins/authentik/README.md).

## Create the token

1. **A service account**: *Directory → Users → Create service account*. The password it shows is an app
   password and is not used here.
2. **Its rights**, by group: `authentik Read-only` (shipped with authentik) to read everything, or a
   superuser group such as `authentik Admins` when you want Claude to make changes. Start read-only.
3. **An API token**: *Directory → Tokens and App passwords → Create*, intent *API Token*, for that account,
   with **Expiring off**. An expiring API token lasts a day by default.

## Configure

| Variable | Required | Purpose |
|---|---|---|
| `AUTHENTIK_HOST` | yes | Address of the server; `/api/v3` is added when absent |
| `AUTHENTIK_TOKEN` | yes | The API token |
| `AUTHENTIK_CA_CERT` | no | CA bundle for a privately signed certificate |
| `AUTHENTIK_VERIFY_TLS` | no | `0` turns certificate verification off. On by default |
| `AUTHENTIK_TIMEOUT` | no | HTTP timeout in seconds, default 20 |

## What to ask

Start with `/authentik:doctor`. Then, for example:

- "Bob gets access denied on Dashboards but Alice gets in. Why?"
- "Give me a health report of my SSO."
- "Add an OIDC application for the wiki; only the staff group may use it."
- "Which applications can every user open?"
- "Who is a superuser, and which API tokens never expire?"
- "Show me the failed logins since this morning."

## Try it without a server

```
export AUTHENTIK_MOCK_DIR=$PWD/plugins/authentik/skills/authentik/evals/fixtures
python3 plugins/authentik/skills/authentik/scripts/authentik.py apps bindings dashboards --table
```

The fixtures describe a synthetic server; writes are echoed back and never stored.

## Safety

- Read commands are pre-approved when you start from `/authentik:authentik <question>` or
  `/authentik:doctor`; otherwise your permission mode may ask for them. Every write prints the exact request
  and stops unless `--yes` is given; `--dry-run` prints it without sending. Writes and `raw` are never
  pre-approved, so your permission mode asks too.
- Claude shows one plan per change and says what it does to who can log in where. Every plan that touches a
  binding names the application's policy engine mode, because one more binding widens access under `any` and
  narrows it under `all`.
- OAuth2 client secrets, which authentik returns in every provider read, and other credentials are redacted
  unless `--show-secrets` is passed.
- Claude does not create tokens or read their keys, set passwords, create recovery links or impersonate
  users. Default flows, stages, brands and `managed` objects are left alone unless you ask for exactly that.

See also the [safety model](safety.md).

## Not covered

Installing and upgrading authentik; writing flows, stages, expression policies and blueprints; the client
side of an application's SSO setup. Sources, stages, certificates and RBAC roles are reachable through `raw`
only.
