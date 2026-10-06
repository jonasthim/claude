# Pangolin

Operates a self-hosted [Pangolin](https://github.com/fosrl/pangolin) server, the tunnelled reverse proxy
built on Newt, Gerbil and Traefik, through its Integration API: published resources, sites and their tunnels,
targets and health checks, access rules, and who may open what. The bundled CLI (`pangolin.py`) is
standard-library Python 3.8+.

Needs a Pangolin server with the Integration API enabled, reachable from where Claude Code runs. Written
against Pangolin 1.24 from its source and documentation; it has not been run against a live server yet.

Full reference: [plugins/pangolin/README.md](https://github.com/jonasthim/claude/blob/main/plugins/pangolin/README.md).

## Create the key

1. **Enable the Integration API** if it is off: `flags.enable_integration_api: true` in Pangolin's
   `config.yml`, and a route to its port (3003 by default), usually `api.<your domain>`.
2. **Create an API key** in the dashboard: *Organization → API Keys*. Give it only the list and get
   permissions for a read-only plugin; add create, update and delete for sites, resources, targets and rules
   when you want Claude to make changes. The key is shown once. A root key (*Server Admin → API Keys*) works
   across organizations.

## Configure

| Variable | Required | Purpose |
|---|---|---|
| `PANGOLIN_HOST` | yes | Address of the Integration API (not the dashboard); `/v1` is added when absent |
| `PANGOLIN_API_KEY` | yes | The API key, `<id>.<secret>` |
| `PANGOLIN_ORG` | for organization keys | Organization id, the slug in the dashboard address |
| `PANGOLIN_CA_CERT` | no | CA bundle for a privately signed certificate |
| `PANGOLIN_VERIFY_TLS` | no | `0` turns certificate verification off. On by default |
| `PANGOLIN_TIMEOUT` | no | HTTP timeout in seconds, default 20 |

## What to ask

Start with `/pangolin:doctor`. Then, for example:

- "Why is app.example.com a bad gateway?"
- "Give me a health report of everything I publish."
- "Which of my published apps have no login in front of them?"
- "Publish the service on 198.51.100.5:8080 as notes.example.com behind SSO."
- "Add a health check to the wiki's second target."
- "Take status.example.com offline without deleting it."

## Try it without a server

```
export PANGOLIN_MOCK_DIR=$PWD/plugins/pangolin/skills/pangolin/evals/fixtures
python3 plugins/pangolin/skills/pangolin/scripts/pangolin.py report health --table
```

The fixtures describe a synthetic organization; writes are echoed back and never stored.

## Safety

- Read commands are pre-approved when you start from `/pangolin:pangolin <question>` or `/pangolin:doctor`;
  otherwise your permission mode may ask for them. Every write prints the exact request and stops unless
  `--yes` is given; `--dry-run` prints it without sending. Writes are never pre-approved, so your permission
  mode asks too.
- Claude shows one plan per change and says what it does to availability and to protection: turning off SSO,
  clearing a password or emptying the allowed roles can open an application to the internet.
- A new service is created as resource, then access settings, then targets, so nothing is served before its
  login is in place.
- Site secrets, access tokens, identity-provider client secrets and credential-looking header values are
  redacted unless `--show-secrets` is passed.

See also the [safety model](safety.md).

## Not covered

Installing and upgrading Pangolin; Traefik and CrowdSec configuration; the Newt and Olm clients; request
logs. Private resources, resource policies, clients and blueprints are reachable through `raw` only.
