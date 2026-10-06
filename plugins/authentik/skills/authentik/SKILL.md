---
name: authentik
description: Operate an authentik identity provider (goauthentik) through its REST API. Use this whenever the user mentions authentik, their identity provider, IdP or SSO server, an authentik application, provider, outpost, flow, policy or binding, or asks "why can't X log in to Y", "access denied after login", "who can open this app", "add an OIDC / OAuth2 / SAML / proxy app", "give this group access", "is the outpost connected", "who is a superuser", "what failed logins were there", or wants a health report or an access audit of their SSO, even when they only say "the login server" and their setup is authentik. Reads are free; every change goes through a plan → confirm → apply → verify protocol. Do NOT use for Keycloak, Authelia, Zitadel, Okta or Entra ID, for writing the OIDC client side of an application, or for installing and upgrading the authentik server itself.
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py info*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py report *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py apps list*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py apps get *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py apps bindings *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py apps access *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py apps used-by *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py providers list*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py providers get *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py providers setup-urls *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py providers used-by *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py groups list*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py groups get *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py groups members *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py groups used-by *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py users *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py bindings list*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py bindings get *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py policies list*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py policies get *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py flows *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py outposts *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py events *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py mappings *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py tokens *)
---

# authentik operations

You are operating the identity provider that every other login depends on. Reading is cheap and
safe. Writing can lock everyone out, the user included, or quietly let everyone into an
application. The bundled CLI makes the safe path the easy path: it handles the token,
pagination and name lookups, explains what an application's bindings mean, and refuses to send
a change until `--yes` is given.

Facts here and in the references come from the authentik 2026.8.3 OpenAPI schema, source and
documentation. Items marked UNVERIFIED were not confirmed; nothing is marked as checked against
a live server yet. The server's own API browser at `<address>/api/v3/` is the truth for the
version that is running.

`$A` below stands for `python3 ${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py`.
Write the full command in every call: shell variables do not survive between calls, and the
read-only commands are pre-approved by their full form, one plain command at a time (no pipes,
no `&&`). Writes and `raw` are not pre-approved, so the session asks the user as well. If a read is
refused for lack of approval, do not look for another way to run it: tell the user, and that
starting from `/authentik:authentik` or allowing the read commands avoids it.

```
$A --help          # groups: info apps providers groups users bindings policies flows outposts events mappings tokens report raw
$A apps --help     # verbs per group
```

Output is JSON by default; add `--table` to any list for a markdown table. This is the whole
surface:

```
info                                                   report health
apps       list|get|bindings|access [--user U]|used-by|create|create-with-provider|update|patch|delete   <slug>
providers  list|get|setup-urls|used-by|create|update|patch|delete   <id>  [--type oauth2|proxy|ldap|saml|...]
groups     list|get|members|used-by|create|update|patch|delete|add-user <group> <user>|remove-user <group> <user>
users      list|get <pk|username>|find <text>          bindings   list [--target UUID]|get|create|update|patch|delete|used-by
policies   list|get|used-by|delete                     flows      list|get|used-by
outposts   list|get|health <uuid|name>                 events     list     mappings|tokens  list
raw <METHOD> <path> [--query k=v ...] [--all] [--body ...]
flags: --table  --filter key=value  --limit N  --body <file|-|json>  --dry-run  --yes  --show-secrets
```

Applications and flows are named by slug, providers and users by number, everything else by
UUID. The CLI accepts a group's or outpost's exact name and a username wherever an id is asked
for. `events list` is newest first and stops at 50 unless `--limit` says otherwise.

## First call of a session

Run `$A info`. It prints who the token is (`username`, `is_superuser`, `groups`) and the server
version. Outcomes:

- A `user` object: good. Note `is_superuser`. A token that is not a superuser sees only what
  its user may view: **lists come back shorter, without an error**. Whenever something is "not
  there", say whose eyes you are looking through before concluding that it does not exist.
- `error[setup]`: the environment is not configured. Point the user at the README's setup
  section (`AUTHENTIK_HOST`, `AUTHENTIK_TOKEN`) and stop. Never ask for the token in chat.
- `error[auth]`: the token was rejected. authentik answers HTTP 403 for a bad token as well as
  for a missing permission; the CLI tells them apart by the message. The usual cause is expiry:
  an API token expires after a day by default and is then rotated, so a token meant for a tool
  must be created as non-expiring. An app password is not an API token and never works here.
- `error[permission]`: the token is valid and its user lacks the permission for that call.
- `error[timeout]`, `error[dns]`, `error[refused]`, `error[certificate]`: a connection problem;
  the message says what to check. Never set `AUTHENTIK_VERIFY_TLS=0` yourself.

`/authentik:doctor` runs the same check and explains each failure.

## Pick the workflow

Detailed steps are in `references/playbooks.md`; read the matching section before starting.

| User says | Workflow | Start with |
|---|---|---|
| "X can't log in to Y", "access denied", "it worked yesterday" | §1 Login troubleshooting | `$A apps access <slug> --user <username>`, `$A apps bindings <slug> --table` |
| "how is my SSO", "health report", "anything broken" | §2 Health report | `$A report health` |
| "add an OIDC app", "set up SSO for Grafana" | §3 Add an application | `$A flows list --table`, `$A mappings list --table` |
| "give the team access", "remove Bob", "only admins may open it" | §4 Change who may open an application | `$A apps bindings <slug> --table` |
| "is the outpost connected", "proxy / LDAP login broken" | §5 Outposts | `$A outposts list --table` |
| "who is an admin", "which apps are open to everyone", "audit" | §6 Access audit | `$A report health`, `$A groups list --filter is_superuser=true` |
| "what happened", "failed logins", "who changed this" | §7 Event log | `$A events list --table --filter ...` |

Endpoints, fields, filters and error shapes are in `references/api.md`.

## Who may open an application

This is the question behind most tasks, and authentik answers it with **bindings** on the
application plus one setting, `policy_engine_mode`:

- No enabled binding: every user who can log in may open it.
- `any` (the default): a user must pass at least one enabled binding.
- `all`: a user must pass every enabled binding.

A binding points at a group, a user or a policy. So the same act has opposite effects: adding
a second group binding **widens** access under `any` and **narrows** it under `all`. `$A apps
bindings <slug>` prints the mode, what it means and the bindings in order; read it before
planning, and put the mode into every plan that adds, removes, enables or disables a binding.
`$A apps access <slug> --user <username>` asks authentik itself for the verdict (superuser
tokens only: for anyone else the API silently answers for the token's own user, so the CLI
refuses instead).

## The change protocol

Every mutating verb (`create`, `update`, `patch`, `delete`, `add-user`, `remove-user`,
`create-with-provider`, and any non-GET `raw`) behaves the same way.

1. Without `--yes` the script prints the exact method, URL and body and exits with code 3. That
   output is your plan. Show it to the user in words plus the body, together with the current
   state and the effect on who can log in where.
2. Wait for the user to confirm in a later message. One confirmation covers one change.
3. Re-run with `--yes`. A 400 names the field; fix only that and show the corrected body
   before sending again.
4. Verify by reading the object back, and for access changes by running `apps access` for one
   user who should get in and one who should not. Report before and after in a few lines.

Use `patch` for a change to one field: `update` is a PUT and replaces the whole object, so a
field you leave out is reset. Run `<group> used-by <id>` before any `delete`.

Things that deserve an explicit warning in the plan, because they lock people out or let
people in:

- **Flows, stages and brands.** Every login runs through the default flows. Do not change or
  delete them through `raw` unless the user asks for exactly that change, and say that a
  mistake there locks out every user, including the one who could fix it.
- **Superuser groups.** Removing a user from a group with `is_superuser: true`, or deleting
  it, can remove the last administrator. Adding to it grants everything.
- **The credential in use.** Deleting or changing the token's own user, group or token ends
  the session.
- **Managed objects.** A non-empty `managed` field means authentik or a blueprint owns the
  object and will overwrite a change. Leave these alone and say why.
- **Removing the last binding** of an application opens it to every user. Switching
  `policy_engine_mode` changes who gets in without touching a single binding.
- **Providers and outposts.** A proxy, LDAP, RADIUS or RAC provider works only while an outpost
  lists it; removing it from the outpost is an outage for that application.

Not done through this skill, whatever the token allows: creating tokens or reading their keys,
setting passwords, creating recovery links, and impersonating a user. Each puts a working
credential for someone's account into the conversation. The user does these in authentik.

## Secrets

authentik returns OAuth2 `client_secret` values in cleartext in every provider read, and other
calls return cookie secrets, RADIUS shared secrets, kubeconfigs and service-account
credentials. `/admin/system/` echoes the request's own headers, token included.
`authentik.py` redacts credential-shaped keys in everything it prints, dry-run bodies included,
unless `--show-secrets` is passed, and never prints its own token, with or without that flag. Use that flag only when the user asked for
the value itself and it goes straight to them, never into a report, a file or a commit. To
hand a new application its client secret, tell the user where to read it in authentik
(Applications → Providers → the provider), or print it once with `--show-secrets` when they
ask for that.

## Answering

Lead with the conclusion, then the evidence as a markdown table or a few bullets (the mode,
the bindings, the group memberships, the event), then the next step. Name applications and
groups as authentik shows them; hide UUIDs unless the user needs one. When the answer depends
on what the token can see, say so in one line. Keep raw JSON for when it is asked for.

## Testing without a server

`AUTHENTIK_MOCK_DIR="${CLAUDE_PLUGIN_ROOT}/skills/authentik/evals/fixtures"` makes every command
answer from a synthetic server (five applications, five providers, two outposts). Writes are
echoed back, not stored. Use it to demonstrate a flow or check a body before touching the real
server.
