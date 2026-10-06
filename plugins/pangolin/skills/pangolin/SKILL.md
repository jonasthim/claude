---
name: pangolin
description: Operate a self-hosted Pangolin server (fosrl/pangolin, the tunnelled reverse proxy with Newt, Gerbil and Traefik) through its Integration API. Use this whenever the user mentions Pangolin, Newt, a Pangolin site, resource or target, or asks to publish, expose or unpublish a service on their Pangolin domain, to put login or SSO in front of an app, "why is app.example.com giving a bad gateway / 404 / login loop", "which of my published apps have no authentication", "is the tunnel up", "add a second backend", "set up a health check", or wants a health report of what they publish, even when they only say "my reverse proxy" or "the tunnel" and their setup is Pangolin. Reads are free; every change goes through a plan → confirm → apply → verify protocol. Do NOT use for nginx, Caddy, Cloudflare Tunnel or a standalone Traefik, or for installing and upgrading the Pangolin server itself.
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py info*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py access*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py report *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py orgs list*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py sites list*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py sites get *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py resources list*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py resources find *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py resources get *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py resources auth *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py targets list *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py targets get *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py rules list *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py domains *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py idps list*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py roles list*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py users list*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py clients list*)
---

# Pangolin operations

You are operating the front door of someone's services. Reading is cheap and safe. Writing can
take an application offline or, worse, put one on the internet without a login. The bundled CLI
makes the safe path the easy path: it handles the key, the response envelope, both pagination
styles and name lookups, and it refuses to send a change until `--yes` is given.

Facts here and in the references come from the Pangolin 1.24.0 source. Items marked UNVERIFIED
were not confirmed; nothing is marked as checked against a live server yet. The API's own
Swagger page at `<API address>/v1/docs` is the truth for the version that is running, and the
project warns that routes may change between releases.

`$P` below stands for `python3 ${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py`. Write
the full command in every call: shell variables do not survive between calls, and the read-only
commands are pre-approved by their full form, one plain command at a time (no pipes, no `&&`).
Writes and `raw` are not pre-approved, so the session asks the user as well. If a read is
refused for lack of approval, do not look for another way to run it: tell the user, and that
starting from `/pangolin:pangolin` or allowing the read commands avoids it.

```
$P --help             # groups: info access orgs sites resources targets rules domains idps roles users clients report raw
$P resources --help   # verbs per group
```

Output is JSON by default; add `--table` to any list for a markdown table that includes the id
column. This is the whole surface:

```
info                         access                       orgs list            (root keys only)
sites      list|get|create|update|delete [--delete-resources]      <id | niceId>
resources  list|find <text>|get|auth|create|update|enable|disable|delete   <id | niceId | full domain>
targets    list <resource>|get <targetId>|create <resource>|update <targetId>|delete <targetId>
rules      list <resource>|create <resource>|update <resource> <ruleId>|delete <resource> <ruleId>
domains    list|get|dns-records      idps|roles|users|clients  list
report health                raw <METHOD> <path> [--query k=v ...] [--all] [--body ...]
flags: --table  --filter key=value  --limit N  --org ID  --body <file|-|json>  --dry-run  --yes  --show-secrets
```

`resources list` adds three readable columns to what the API returns: `auth` (which of sso,
password, pincode, header, whitelist is set, or `none`), `targetHealth` and `siteNames` (with
`(offline)` after a site whose tunnel is down). A resource can be named by its numeric id, its
niceId or its full domain; the CLI resolves the last two with an exact match and stops when the
name is ambiguous.

## First call of a session

Run `$P info`. Outcomes:

- `"api": {"message": "Healthy"}` and an `org` object: good, continue.
- `error[setup]`: the environment is not configured. Point the user at the README's setup
  section (`PANGOLIN_HOST`, `PANGOLIN_API_KEY`, `PANGOLIN_ORG`) and stop. Never ask for the key
  in chat; it belongs in the environment, not the transcript.
- `error[auth]` (HTTP 401): the key is wrong or was deleted. `error[permission]` (HTTP 403): the
  message says which of three things is missing: an action (`Key does not have permission
  perform this action`), the organization (`Key does not have access to this organization`) or
  root (`Key does not have root access`). Say which; the user fixes it in the dashboard.
- `error[timeout]`, `error[dns]`, `error[refused]`, `error[certificate]`: a connection problem;
  the message says what to check. Never set `PANGOLIN_VERIFY_TLS=0` yourself, that is the
  user's call. `error[not-json]` usually means `PANGOLIN_HOST` points at the dashboard instead of
  the API address.

An API key holds a list of allowed actions and cannot read that list itself. `$P access --table`
asks for one row of each common list and shows which reads work; a write permission can only be
found out by trying the write. `/pangolin:doctor` runs both checks and explains each failure.

## Pick the workflow

Detailed steps are in `references/playbooks.md`; read the matching section before starting.

| User says | Workflow | Start with |
|---|---|---|
| "app.example.com is down / bad gateway / 404" | §1 Resource troubleshooting | `$P resources get <domain>` then `$P targets list <domain> --table` |
| "how is everything", "health report", "what is exposed without login" | §2 Health and exposure report | `$P report health --table` |
| "publish this app", "expose 198.51.100.5:8080 as app.example.com" | §3 Publish a service | `$P sites list --table`, `$P domains list --table` |
| "add a backend", "change the port", "add a health check" | §4 Change targets | `$P targets list <resource> --table` |
| "require login", "turn on SSO", "skip the Pangolin login page", "block /admin" | §5 Access control | `$P resources auth <resource>` |
| "is the tunnel up", "site offline" | §6 Site troubleshooting | `$P sites list --table` |
| "what do I publish", "list sites / domains / users" | plain read | `$P <group> list --table` |

Endpoints, body fields, pagination and error meanings are in `references/api.md`.

## The change protocol

Every mutating verb (`create`, `update`, `enable`, `disable`, `delete`, and any non-GET `raw`)
behaves the same way.

1. Without `--yes` the script prints the exact method, URL and body and exits with code 3. That
   output is your plan. Show it to the user in words plus the body, together with the current
   state it changes and the effect (see the impact table in playbooks §7).
2. Wait for the user to confirm in a later message. One confirmation covers one change. A new
   service is a resource, then its access settings, then one or more targets: three or more
   plans, applied in that order (so nothing is served before its login is in place) and
   verified one by one.
3. Re-run with `--yes`. Bodies are strict: an unknown field is a 400 that names it. Fix only
   that and show the corrected body before sending again.
4. Verify by reading the object back (`resources get`, `targets list`), and for a target wait
   for `hcHealth` to leave `unknown`. Report before and after in two or three lines.

In this API **PUT creates and POST updates**; the CLI's verbs already map to the right one, and
it matters when you use `raw`. Two update calls demand fields you are not changing: a target
update needs `siteId` and `ip`, a rule update needs `priority`. The CLI copies them from the
current object, so `--body '{"port": 8081}'` is enough.

Things that deserve an explicit warning in the plan:

- **Removing protection.** Setting `sso` to false, clearing a password or pincode, or emptying
  the roles and users of a resource can leave it reachable by anyone on the internet. State
  what protection remains after the change (`resources auth` shows it).
- **Outages.** `resources disable` or `delete`, deleting or disabling the last enabled target,
  and `sites delete` take the application offline at once. `sites delete --delete-resources`
  removes every resource on that site.
- **Rules.** A `DROP` rule that matches the user's own address or country locks them out, and
  rules only apply when the resource has `applyRules: true`.
- **Shared policies.** `sso`, `emailWhitelistEnabled`, `applyRules` and `skipToIdpId` on a
  resource write to its inline policy and override a shared policy assigned to it.
- **Domains, identity providers, users, roles and API keys** have no verbs in the CLI on
  purpose. Reach them with `raw` only when the user asks for exactly that change.

Sites behind a Newt tunnel receive target changes over the tunnel's own connection; no restart
of Newt is needed (from the 1.24.0 source). What happens while the site is offline is
UNVERIFIED, so check `online` first and say so when it is not.

## Secrets

The API returns some credentials in cleartext: a new site's `secret`, a generated
`accessToken`, a new API key, a target's `authToken`, an identity provider's `clientSecret`,
and whatever the operator put in `hcHeaders` or request headers. `pangolin.py` redacts
credential-shaped keys and credential-looking header values in everything it prints, dry-run
bodies included, unless `--show-secrets` is passed. Use that flag only when the user asked for
the value itself and it goes straight to them, never into a report, a file or a commit.

Resource passwords and pincodes are the user's to choose. Do not invent one or put one on a
command line: ask the user to set it in the dashboard, or to write the body to a file and pass
`--body <file>`. Creating a site returns the Newt credentials once, into the transcript, so site
creation is better done by the user in the dashboard unless they ask you to do it.

## Answering

Lead with the conclusion, then the evidence as a markdown table or a few bullets with the
values that matter (enabled, auth, site online, target address, `hcHealth`), then the next
step. Hide ids unless the user needs them for a follow-up; keep raw JSON for when they ask.
`health: unknown` means no health check is configured, not that the target is fine: say "not
monitored". If the API cannot see something (Traefik's own logs, certificate issuance, what
the backend answered), say so in one line and name where to look (`references/playbooks.md` §8).

## Testing without a server

`PANGOLIN_MOCK_DIR="${CLAUDE_PLUGIN_ROOT}/skills/pangolin/evals/fixtures"` makes every command
answer from a synthetic organization (three sites, six resources, eight targets). Writes are
echoed back, not stored. Use it to demonstrate a flow or check a body before touching the real
server.
