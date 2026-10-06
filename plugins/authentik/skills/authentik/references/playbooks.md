# Playbooks

Step-by-step flows for the jobs this skill is used for most. `A` below means
`python3 "${CLAUDE_PLUGIN_ROOT}/skills/authentik/scripts/authentik.py"`.

## Contents
1. Login troubleshooting ("X can't log in to Y")
2. Health report
3. Add an application
4. Change who may open an application
5. Outposts
6. Access audit
7. Event log
8. Impact table
9. What the API cannot see
10. Health report template

---

## 1. Login troubleshooting

A login has two halves, and the fix is in a different place for each: **authentication** (the
user proves who they are to authentik) and **authorization** (authentik decides whether that
user may open this application). Find out which half failed before reading anything else.

1. **Who and what.** `A users get <username>` (is the account active? when was the last
   login?) and `A apps get <slug>` (does it have a provider?).
2. **Ask authentik for the verdict.** `A apps access <slug> --user <username>`.
   - `passing: true`: authentik lets this user in. The problem is on the application's side or
     in the protocol setup; go to step 5.
   - `passing: false`: authorization. Go to step 3.
   - The CLI refuses because the token is not a superuser: do step 3 by reading, and say that
     the verdict could not be asked for.
3. **Read the bindings.** `A apps bindings <slug> --table` prints the engine mode, what it
   means, and each binding in order. Then `A groups members <group> --table` for each group
   binding. Typical findings:
   - Mode `all` with two group bindings, and the user is in one group only. Under `all` the
     user needs every binding; the person who added the second group meant `any`.
   - The user is in no bound group.
   - A binding with `negate: true`, or a policy binding whose policy fails or errors
     (`failure_result: false` turns an error into a denial).
   - No enabled bindings at all means everyone gets in, so a denial is not from here.
4. **Did they get as far as the application?** `A events list --table --filter
   username=<username> --limit 20`.
   - `login_failed`: authentication. Wrong password, an MFA problem, or an inactive account.
     The flow's stages decide; the user or an admin fixes the account in authentik.
   - `login` followed by no `authorize_application` for this application: authorization
     (step 3). A plain denial by a binding leaves no event of its own.
   - `authorize_application` for this application: authentik did let them in; step 5.
5. **The application side.** `A events list --table --filter action=configuration_error` and
   `A providers get <id> --type <type>` (`id` is the application's `provider`).
   - OAuth2: the application's redirect address must match `redirect_uris` exactly (scheme,
     host, path, trailing slash). `A providers setup-urls <id>` prints the issuer and endpoint
     addresses the application must be configured with.
   - Proxy, LDAP, RADIUS: the provider must be on an outpost and the outpost connected (§5).
   - The application's own log is where a rejected token or a wrong client secret shows up.
     authentik's side looks successful in that case; say so.
6. **Answer.** Lead with which half failed and the one cause, then the evidence, then the fix.
   If the fix is a change (add the user to a group, change the mode), offer it and go through
   the change protocol.

## 2. Health report

`A report health` gathers it in one call. Read each part:

| Field | Meaning | Usually |
|---|---|---|
| `version.outdated`, `version.outpost_outdated` | A newer release exists; an outpost runs an older version than the server | Worth a line; upgrading is the user's job |
| `outposts[].instances` 0 | No instance of that outpost is connected | Everything behind it is down |
| `outposts[].outdated` | Instances older than the server | Upgrade the outpost with the server |
| `providersNotOnAnOutpost` | A proxy, LDAP, RADIUS or RAC provider that no outpost serves | That application cannot work |
| `providersWithoutApplication` | A provider nothing uses | Left over; candidate for cleanup |
| `applicationsWithoutProvider` | Only a link on the user's page | Often intended |
| `applicationsOpenToAllUsers` | No enabled binding: every user who can log in may open it | Ask whether that is intended |
| `applicationsRequiringEveryBinding` | Mode `all` with more than one binding | Check it is not the `all` mistake of §1 |
| `recentEvents.counts` | Trouble among the newest events (`sampled` of them, back to `oldest`) | Read the ones that are there with §7 |

The report is what the token's user can see. If `superuser` is false, say that lists may be
incomplete. Use the template in §10.

## 3. Add an application

One transactional call creates the application, its provider and its bindings together, or
nothing at all. This example is OAuth2/OIDC, the common case.

1. **Discover.**
   - `A apps list --table` and `A providers list --table`: the slug and the provider name are free.
   - `A flows list --table --filter designation=authorization` and `... designation=invalidation`:
     the UUID (`pk`) of the authorization flow (explicit or implicit consent) and of the
     provider invalidation flow.
   - `A mappings list --table`: the UUIDs of the `openid`, `email` and `profile` scope mappings.
   - `A groups list --table`: the group that may open it, if access is to be limited.
2. **Ask what you cannot read**: the application's redirect address (from its documentation or
   its settings page, exactly), whether it needs anything beyond the three standard scopes, and
   who may use it.
3. **Plan.** Write the body from `references/api.md` §10 to a file and show it:
   `A apps create-with-provider --body app.json`. State who will be able to open it: with no
   `policy_bindings` that is every user.
4. **Apply** with `--yes` after the user confirms. The CLI fails when authentik answers
   `applied: false`, and prints its `logs`.
5. **Read back.** `A apps get <slug>` (note `provider`), `A apps bindings <slug> --table`,
   `A providers setup-urls <provider id>`.
6. **Hand over** what the application needs: the issuer or discovery address and the endpoint
   addresses from `setup-urls`, the client id from `A providers get <id> --type oauth2`, and
   the client secret. The secret is redacted; tell the user where to read it in authentik, or
   print it once with `--show-secrets` if they ask for that.
7. **Verify.** `A apps access <slug> --user <someone who should get in>` and one who should
   not; then the user tries a real login. `A events list --filter context_authorized_app=<name>`
   shows it arriving.

For a proxy, LDAP or RADIUS application there is one more step: the new provider must be added
to an outpost's `providers` (§5), which is a change to the outpost.

## 4. Change who may open an application

Always start with `A apps bindings <slug> --table`, and name the mode in the plan.

| Wish | Change | Under `any` | Under `all` |
|---|---|---|---|
| Let one more person in | `A groups add-user <bound group> <username>` | They get in | They get in only if they pass every other binding too |
| Let another group in | `A bindings create --body '{"target": "<pbm_uuid>", "group": "<uuid>", "order": 1}'` | Access widens | Access **narrows** to people in both groups |
| Take a person out | `A groups remove-user <group> <username>` | Out, unless another binding passes them | Out |
| Limit an open application | Create its first binding | Only the bound group gets in | The same |
| Open it to everyone | Delete or disable every binding | | |
| Switch the mode | `A apps patch <slug> --body '{"policy_engine_mode": "any"}'` | | Changes who gets in without touching a binding |

- A group change reaches every application and every other thing that group is bound to:
  `A groups used-by <group>` before adding to or removing from it.
- Prefer disabling a binding (`A bindings patch <uuid> --body '{"enabled": false}'`) to
  deleting it when the user may want it back.
- After the change: `A apps access <slug> --user <username>` for one user who should get in
  and one who should not.

## 5. Outposts

1. `A outposts list --table`: name, type, the provider ids it serves, and `managed` (the
   embedded outpost has a value there).
2. `A outposts health <name> --table`: one row per connected instance with its version and
   `last_seen`. No rows: nothing is connected. `version_outdated: true`: the outpost is older
   than the server; upgrade it together with the server.
3. `A report health` lists `providersNotOnAnOutpost`. A proxy provider that is on no outpost is
   a common reason a freshly added proxy application does not work.
4. Adding a provider to an outpost is a `patch` of its `providers` list, sent through `raw`
   (`A raw PATCH /outposts/instances/<uuid>/ --body '{"providers": [3, 5]}'`). The list
   replaces the old one, so build it from the current value plus the new id, and say that
   leaving one out takes that application down.
5. Why an instance is not connected is outside the API: the outpost's own container or
   service, its `AUTHENTIK_HOST` setting, and whether it can reach the server (§9).

## 6. Access audit

| Question | Read |
|---|---|
| Who has full control? | `A groups list --filter is_superuser=true --table`, then `A groups members <group> --table` for each |
| Which accounts are unused? | `A users list --table --filter is_active=true --filter ordering=last_login`; `--filter last_login__isnull=true` for never used |
| Which applications can everyone open? | `applicationsOpenToAllUsers` in `A report health` |
| Which tokens exist? | `A tokens list --table`: `intent`, owner, `expiring`. A non-expiring API token for a superuser is the strongest credential there is; list them |
| What can one user open? | `A apps list --table --filter for_user=<pk>` (superuser tokens) |
| Who changed configuration recently? | `A events list --table --filter action=model_updated --limit 30` |
| Who read a secret? | `A events list --table --filter action=secret_view` |

Report findings as a list the user can act on, most serious first. Do not change anything in
an audit; each fix is its own change with its own plan.

## 7. Event log

`A events list --table` shows the newest 50. Narrow with `--filter`:

```
--filter username=alice                        one user's trail
--filter action=login_failed                   failed logins (add client_ip=... for one source)
--filter context_authorized_app=Wiki           who was let into an application
--filter action=configuration_error            setup problems authentik noticed itself
--filter context_model_name=application        configuration changes to applications
```

There is no date filter; raise `--limit` and stop reading at the time that matters. Without
`--table` each event carries its full `context`, which is where the detail is. Many failed
logins for many usernames from one address is guessing; many for one username from that
user's own address is a forgotten password. Say which it looks like.

## 8. Impact table

| Change | Effect | Undo |
|---|---|---|
| `groups add-user` | The user gains everything bound to that group, everywhere | `groups remove-user` |
| `groups remove-user` | The user loses it; from a superuser group, possibly the last admin | `groups add-user`, if anyone can still log in |
| `bindings create` on an application | Widens access under `any`, narrows it under `all`; the first binding closes an open application | Delete or disable it |
| `bindings delete`, or disabling the last one | Can open the application to every user | Recreate |
| `apps patch` of `policy_engine_mode` | Changes who gets in | Patch it back |
| `apps delete` | Logins to it stop; the provider remains | Recreate and re-link the provider |
| `providers delete` | The application can no longer log anyone in; its client secret is gone | Recreate and reconfigure the application |
| `providers patch` of `redirect_uris` or the flows | Logins fail at once if wrong | Patch it back |
| `groups delete` | Every binding to it goes with it or dangles (UNVERIFIED which) | Recreate group and bindings |
| Any change to a default flow, stage or brand | Can stop every login | Only by someone who still has a session |

## 9. What the API cannot see

- Why an application rejected a login that authentik approved: the application's own log.
- Why an outpost is not connected: the outpost's container or service log and its network path.
- Server health below the API (database, cache, workers): `A raw GET /admin/system/` and
  `A raw GET /tasks/workers/` show a little, to a user with the system-info permission; the
  server's own logs show the rest.
- What a user actually saw in the browser. Ask them for the exact message.

## 10. Health report template

```
**authentik <version>: <one-line verdict>** (as <username>, superuser: yes/no)

| | Count | Attention |
|---|---|---|
| Applications | 5 | 2 open to every user: files, legacy-portal (intended?) |
| Providers | 5 | 1 on no outpost: Files; 1 unused: Old cloud |
| Outposts | 2 | ldap-outpost runs 2026.5.1, server 2026.8.3 |
| Recent events | 200 sampled | 2 failed logins, 1 configuration error |

**Needs action**
- <object>: <cause in one line, with the value that shows it> → <next step>

**Worth a look**
- <open applications, unused providers, mode "all" with several bindings>
```
