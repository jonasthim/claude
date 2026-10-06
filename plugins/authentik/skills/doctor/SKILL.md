---
name: doctor
description: "Slash command: check the authentik connection from this machine with read-only calls (who the token is, the server version, what the token can see), and explain how to fix each failure."
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py info*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py apps list*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py groups list*)
---

# /authentik:doctor

Read-only connection check; it takes no arguments, so ignore `$ARGUMENTS`. If the
`authentik:authentik` skill is not loaded in this conversation, invoke it with the Skill tool.
Never print `AUTHENTIK_TOKEN`; refer to it as "set (hidden)". Run each call as a single plain
command (no pipes, no `&&`) so the pre-approved rule applies.

## Calls, in order (stop only if call 1 fails)

1. `python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py info`: `user` (username,
   type, `is_superuser`, groups) and `version` (`version_current`, `outdated`, `outpost_outdated`).
2. `python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py apps list --limit 5`
3. `python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py groups list --limit 5`

Calls 2 and 3 show what the token can see. authentik filters lists by the user's permissions
instead of refusing, so an empty list from a server that has applications means "may not view",
not "none exist".

## Failures (exit code plus the `error[class]:` line on stderr)

| Class | Meaning | Remedy |
|---|---|---|
| `setup` (exit 1) | A variable is missing | Export `AUTHENTIK_HOST` (for example `https://auth.example.com`) and `AUTHENTIK_TOKEN` in the shell that starts Claude Code |
| `dns`, `refused`, `timeout` | The address does not answer | Check `AUTHENTIK_HOST` and that this machine can reach it |
| `not-json`, `redirect`, `not-found` on call 1 | Something else answers there | `AUTHENTIK_HOST` is the server's base address; the CLI adds `/api/v3` |
| `certificate`, `tls` | The certificate is not trusted, or the port is not https | Use the name on the certificate, or `AUTHENTIK_CA_CERT` for a private CA. Only the user may set `AUTHENTIK_VERIFY_TLS=0`; never set it yourself |
| `auth` | Token rejected (authentik answers 403 `Token invalid/expired`) | It must be an API token: Directory → Tokens and App passwords, intent "API Token". An app password does not work. API tokens expire after a day by default, so create it with "Expiring" off. The user creates it, since its key is a credential |
| `permission` | The token's user may not make that call | Add the user to a group with the needed role: `authentik Read-only` for reading, a superuser group for changes |

## Token capability (from call 1)

| Class | Test | What works |
|---|---|---|
| superuser | `is_superuser` is true | Everything, including access checks for other users and every change. The change protocol and the `--yes` gate are the only protection: say so |
| read-only | Not a superuser, in group `authentik Read-only` | Every list and detail read. Changes fail with 403. `apps access --user` is refused by the CLI, because the API would answer for the token's own user |
| limited | Neither | Lists show only what its roles allow. Name the groups from call 1 and say that results may be incomplete |

## Report

One table `check | result | detail` with the rows `connection`, `token`, `version`, `visibility`;
result is `ok`, `warn` or `fail`. Use `warn` for `version.outdated`, `outpost_outdated`, and for
empty lists on a non-superuser token. Below the table print exactly one line: `ready for <class>
tasks as <username>` or `next step: <the single remedy>`.
