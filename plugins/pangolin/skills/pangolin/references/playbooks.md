# Playbooks

Step-by-step flows for the jobs this skill is used for most. `P` below means
`python3 "${CLAUDE_PLUGIN_ROOT}/skills/pangolin/scripts/pangolin.py"`. A resource can be named
by id, niceId or full domain.

## Contents
1. Resource troubleshooting ("app.example.com is down")
2. Health and exposure report
3. Publish a service
4. Change targets and health checks
5. Access control
6. Site troubleshooting ("is the tunnel up")
7. Impact table
8. What the API cannot see
9. Health report template

---

## 1. Resource troubleshooting

Goal: a diagnosis the user can act on in under a minute of reading, not a data dump. A request
passes through four things, and each can be read: the domain, the resource, the site's tunnel,
the target.

1. **Find the resource.** `P resources get <domain>`. No match: `P resources find <part of the
   name>`, then `P domains list --table`. A domain with `verified: false` or `failed: true`
   (read `errorMessage`) explains certificate errors and names that never resolve to a resource.
2. **Is it switched on?** `enabled: false` means Pangolin is not serving it at all.
3. **What is behind it?** `P targets list <resource> --table`. No targets, or none enabled,
   means there is nothing to send requests to.
4. **Is the tunnel up?** The `siteName` of each target against `P sites list --table`. A site
   with `online: false` makes every target behind it unreachable; continue in §6.
5. **Is the backend answering?** For each target read `hcEnabled` and `hcHealth`.
   - `unhealthy` with the site online: the backend is down, or the address is wrong. Compare
     `hcHostname`, `hcPort` and `hcPath` with `ip` and `port`: the health check keeps its own
     address, and it stays behind when someone changes only the target.
   - `unknown`: no health check is configured, so Pangolin does not know. Say "not monitored"
     and ask the user to test the backend from inside the site's network.
   - `method` `http` against a backend that only speaks `https` (or the reverse) fails even
     though the port is right.
6. **Is it access rather than reachability?** `P resources auth <resource>` and
   `P rules list <resource> --table`. See the table below.
7. **Answer.** Lead with the most likely cause in one sentence, then two to four bullets of
   evidence, then the fix. If the fix is a change, offer it and go through the change protocol.

What the symptom usually points to (check it, do not assume it):

| Symptom | Check first |
|---|---|
| 404 from the proxy | `enabled`, the domain's state, whether any target is enabled |
| 502 or 504 | Site `online`, target `ip`, `port` and `method`, `hcHealth` |
| Certificate warning | `P domains list --table` (`verified`, `failed`, `errorMessage`), the resource's `ssl` |
| Redirected to a login that never completes | `sso`, `skipToIdpId` against `P idps list --table`, the user's roles in `resources auth` |
| "Access denied" after logging in | `roles` and `users` in `resources auth`; `emailWhitelistEnabled` with an empty whitelist |
| Works for some people or places only | `applyRules` and `P rules list`, `blockAccess` |
| Slow or intermittent | Several targets where one is `unhealthy`; `stickySession`; then §8 |

## 2. Health and exposure report

1. `P report health --table`. The summary lists:
   - `sitesOffline`: tunnels that are down.
   - `resourcesUnhealthy`: enabled resources whose health is `unhealthy` or `degraded`, or that
     have an unhealthy target.
   - `resourcesWithoutTargets`: enabled resources with nothing behind them.
   - `resourcesWithoutAuth`: enabled HTTP resources with no SSO, password, pincode, header auth
     or whitelist. Pangolin lets anyone reach these. That can be intended (a public site, an
     application with its own login), so list them and ask, do not call them a fault.
   - `resourcesDisabled`: switched off; candidates for cleanup.
2. `P domains list --table` for domains that failed verification.
3. For each unhealthy resource, `P targets list <resource> --table` and the §1 steps.
4. Count resources whose targets are all `unknown`: those are not monitored, and a report that
   calls them healthy is wrong.
5. Use the template in §9.

## 3. Publish a service

Order matters: create the resource, set its access, and add targets last, so nothing is served
before its protection is in place.

1. **Discover.** `P sites list --table` (the site that can reach the backend, and that it is
   online), `P domains list --table` (the `domainId` of a verified domain),
   `P resources find <subdomain>` (the name is free), `P idps list --table` and
   `P roles list --table` if it will sit behind SSO.
2. **Ask what you cannot read**: the backend's address and port as seen from the site, whether
   it speaks http or https, a health path, and who may use it.
3. **Plan 1, the resource.**
   `P resources create --body '{"name": "Wiki", "domainId": "<domainId>", "subdomain": "wiki", "mode": "http"}'`.
   After the user confirms, add `--yes`, then `P resources get <new id>` and read `sso`, `ssl`
   and `enabled` back: the default of `sso` on a new resource is UNVERIFIED.
4. **Plan 2, access** (§5): SSO on and the roles or users that may enter, or the user's choice
   of another method. If the user wants it public, have them say so.
5. **Plan 3, each target.**
   `P targets create <resource> --body '{"siteId": 1, "ip": "198.51.100.5", "port": 8080, "method": "http", "hcEnabled": true, "hcHostname": "198.51.100.5", "hcPort": 8080, "hcPath": "/"}'`.
   `hcHostname` is required with `hcEnabled`. Use the health path the application documents
   rather than `/` when there is one.
6. **Verify.** `P targets list <resource> --table` until `hcHealth` is `healthy` (it starts as
   `unhealthy` and needs a check interval or two), `P resources auth <resource>`, and ask the
   user to open the address: only they can see the login and the application.

## 4. Change targets and health checks

- **Change one field**: `P targets update <targetId> --body '{"port": 8081}'`. The CLI adds the
  `siteId` and `ip` the API insists on. When the address or port changes, change `hcHostname`
  and `hcPort` in the same body, or the health check keeps probing the old place.
- **Add a backend**: `P targets create <resource> --body ...` as in §3. Several enabled targets
  share the load. A target also has a `priority` (1-1000); how it weighs targets is UNVERIFIED.
- **Take a backend out**: prefer `{"enabled": false}` to deleting, so it can come back. If it
  is the last enabled target, say that the resource goes down.
- **Add a health check to an unmonitored target**: `{"hcEnabled": true, "hcHostname": ...,
  "hcPort": ..., "hcPath": ...}`. `hcHealth` shows `unhealthy` until the first checks pass.
- After any change: `P targets list <resource> --table`, and wait for `hcHealth`.

## 5. Access control

Read first: `P resources auth <resource>` shows `sso`, `blockAccess`, `emailWhitelistEnabled`,
`applyRules`, `skipToIdpId`, the roles and users allowed, and the whitelist. `P resources find
<name> --table` shows in the `auth` column whether a password, pincode or header auth is set.

| Wish | Change |
|---|---|
| Require a Pangolin login | `P resources update <r> --body '{"sso": true}'`, then roles or users |
| Allow a role | `P raw POST /resource/<id>/roles/add --body '{"roleId": 2}'` (ids from `P roles list`) |
| Replace all roles or users | `P raw POST /resource/<id>/roles --body '{"roleIds": [2]}'`; `/users` with `{"userIds": [...]}` |
| Go straight to the identity provider | `P resources update <r> --body '{"skipToIdpId": 1}'` (id from `P idps list`); `null` turns it off |
| Password or pincode | The user sets it in the dashboard, or writes `{"password": "..."}` to a file: `P raw POST /resource/<id>/password --body <file>` |
| Email one-time codes for a list | `{"emailWhitelistEnabled": true}` on the resource, then `P raw POST /resource/<id>/whitelist --body '{"emails": [...]}'` |
| Block or allow by path, address or country | `P rules create <r> --body '{"action": "DROP", "match": "PATH", "value": "/admin/*", "priority": 1}'` and `{"applyRules": true}` on the resource |
| Take it off the internet without deleting | `P resources disable <r>` |

Before any change that removes a method, state what is left afterwards. A resource with `sso`
false and no other method is open to everyone. `raw` needs the numeric id: take `resourceId`
from `P resources get <r>`.

Rules are evaluated by `priority`, lowest first (per the Pangolin documentation). `ACCEPT` lets the request through without
authentication, `DROP` refuses it, `PASS` sends it on to authentication. Rules do nothing until
`applyRules` is true, and an `ACCEPT` on a broad match removes the login for everything it
matches: read the list back in order before applying.

## 6. Site troubleshooting

1. `P sites list --table`: `online`, `type` and `lastPing` (when it last reported).
2. `P resources list --filter siteId=<id> --table` for what depends on it.
3. The API knows that the tunnel is down, not why. Causes to put to the user, most common
   first: the Newt process or container on the site is not running; the site host has no
   outbound connectivity to the Pangolin server; the Newt credentials no longer match (the site
   was recreated); the server's address changed.
4. The fix is on the site host (§8). Do not delete and recreate the site to "reset" it: that
   issues new credentials and, with `--delete-resources`, removes what it published.

## 7. Impact table

| Change | Effect | Undo |
|---|---|---|
| `resources disable` | Offline at once | `resources enable` |
| `resources delete` | Offline; targets, rules and access settings are gone | Recreate by hand |
| `targets delete`, or disabling the last enabled target | Offline, or less capacity | Recreate or re-enable |
| `targets update` of `ip`, `port` or `method` | Requests go to the new place at once | Update back |
| `sso: false`, clearing a password, emptying roles | May open the application to everyone | Set it again |
| `rules create` with `DROP` | Can lock the user out | Delete or disable the rule |
| `rules create` with `ACCEPT` | Skips the login for what it matches | Delete or disable the rule |
| `sites delete` | Every target on it stops working | Recreate; new Newt credentials |
| `sites delete --delete-resources` | The site and all its resources | None |

## 8. What the API cannot see

The API holds configuration and health state. For what a request actually did, the user (or
you, if they give you a shell there) reads logs on the hosts. With the default Docker Compose
install the server's services are `pangolin`, `gerbil` and `traefik`:

```
docker compose logs --tail 200 traefik     # routing, certificates, 502s with the upstream address
docker compose logs --tail 200 pangolin    # authentication decisions, API errors
docker compose logs --tail 200 gerbil      # tunnel endpoints
```

On a site, the Newt client's own log shows connection attempts and the targets it was told
about. Certificate issuance, DNS records at the registrar and the backend's own logs are also
outside the API. Name the place to look instead of guessing.

## 9. Health report template

```
**Pangolin: <one-line verdict>**

| | Count | Attention |
|---|---|---|
| Sites | 3 | 1 offline: Lab |
| Resources | 6 | 2 unhealthy, 1 disabled |
| Not monitored | 3 | no health check |
| Without Pangolin auth | 1 | Files (intended?) |

**Needs action**
- <resource>: <cause in one line, with the value that shows it> → <next step>

**Worth a look**
- <domains that failed verification, disabled resources, unmonitored targets>
```
