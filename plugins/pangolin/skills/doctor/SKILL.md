---
name: doctor
description: "Slash command: check the Pangolin connection from this machine with read-only calls (API health, organization, which read actions the key holds), and explain how to fix each failure."
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py info*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py access*)
---

# /pangolin:doctor

Read-only connection check; it takes no arguments, so ignore `$ARGUMENTS`. If the
`pangolin:pangolin` skill is not loaded in this conversation, invoke it with the Skill tool. Never
print `PANGOLIN_API_KEY`; refer to it as "set (hidden)". Run each call as a single plain command
(no pipes, no `&&`) so the pre-approved rule applies.

## Calls, in order (stop only if call 1 fails)

1. `python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py info`: `api.message` should be
   `Healthy`, `base` is the address in use, `org` is the organization or an `error` with the HTTP
   status.
2. `python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py access`: one row per read
   action with `ok`, `denied` or `error`. A key cannot read its own action list, so this asks for
   one row of each list. `listOrgs` is `denied` for every organization key; that is expected.

## Failures (exit code plus the `error[class]:` line on stderr)

| Class | Meaning | Remedy |
|---|---|---|
| `setup` (exit 1) | A variable is missing | Export `PANGOLIN_HOST` (the API address, usually `https://api.<domain>`), `PANGOLIN_API_KEY` and `PANGOLIN_ORG` in the shell that starts Claude Code |
| `dns`, `refused`, `timeout` | The address does not answer | `PANGOLIN_HOST` must be the Integration API, not the dashboard. It is off by default: `flags.enable_integration_api: true` in the server's `config.yml`, port 3003, and a route to it |
| `not-json`, `redirect` | Something else answers there | Same as above: this is usually the dashboard address |
| `certificate`, `tls` | The certificate is not trusted, or the port is not https | Use the name on the certificate, or `PANGOLIN_CA_CERT` for a private CA. Only the user may set `PANGOLIN_VERIFY_TLS=0`; never set it yourself |
| `auth` (HTTP 401) | Key rejected | The key is `<id>.<secret>` exactly as shown at creation. Create a new one in the dashboard (Organization → API Keys); the user does this, since the key is shown once |
| `permission` (HTTP 403) | The message names it | `...permission perform this action`: add the action to the key. `...access to this organization`: wrong `PANGOLIN_ORG`, or the key belongs to another organization. `...root access`: the call needs a root key |
| `not-found` on `org` | No such organization | `PANGOLIN_ORG` is the organization id, the slug in the dashboard address |

## Report

One table `check | result | detail` with the rows `api`, `organization`, then one row per action
from call 2; result is `ok`, `warn` or `fail`. Below it print exactly one line: `ready for
read-only tasks` when every action except `listOrgs` is `ok`, `ready, with gaps: <actions>` when
some are denied, or `next step: <the single remedy>`. Add that write access cannot be probed
without writing: a change that the key lacks the action for fails with HTTP 403 and names it.
