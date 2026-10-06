# authentik REST API

Condensed from the goauthentik/authentik repository at tag `version/2026.8.3`: the OpenAPI
schema (`schema.yml`), the Python source and the documentation. Not yet confirmed against a
live server; items those sources did not settle are marked UNVERIFIED. The running server
serves its own API browser at `<address>/api/v3/`.

## Contents
1. Addressing and tokens
2. Errors
3. Pagination, search and filters
4. Identifiers
5. Routes
6. Applications, bindings and the engine mode
7. Providers
8. Outposts
9. Events
10. Request bodies
11. Fields that hold credentials
12. Traps

## 1. Addressing and tokens

- Base `https://<host>/api/v3`. **Every path ends with a slash**; without it the server
  redirects and a write is lost. The CLI adds it for `raw`.
- Header `Authorization: Bearer <token>`.
- Only a token with `intent: "api"` authenticates. The other intents (`app_password`,
  `recovery`, `verification`) do not. The token a new service account is given is an
  **app password**, so it does not work here; the account needs a separate API token.
- An API token's `expires` is set by the server to now plus the tenant's
  `default_token_duration` (default one day), whatever the request says, and the documentation
  says expired API tokens are rotated. A token for a tool is created with `expiring: false`.
- A superuser is a member of any group with `is_superuser: true` and passes every check.
- Read-only access: the shipped blueprint "Default - RBAC - Read-only" creates a role and a
  group, both named `authentik Read-only`, holding the view permission on every model. A
  service account in that group reads everything it lists. Whether that role may read
  `/admin/system/` is UNVERIFIED; treat a refusal there as harmless.
- Permissions (roles) control the API. They do not control who may open an application; that
  is bindings (section 6).

## 2. Errors

| Status | Body | CLI class | Meaning |
|---|---|---|---|
| 400 | `{"<field>": ["message"], "non_field_errors": [...]}` | `validation` | Names the field |
| 403 | `{"detail": "Token invalid/expired"}` or `"Malformed header"` | `auth` | Bad, expired or rotated token, or an inactive user |
| 403 | `{"detail": ...}` (other text) | `permission` | The user lacks the permission |
| 404 | `{"detail": ...}` | `not-found` | No such object, or one the user may not view |
| 405 | | `method` | The path has no such method, for example a write to `/providers/all/` |

The schema documents no 401 anywhere: a rejected token is a 403, told apart by its `detail`.
The exact texts for "no credentials" and "no permission" are UNVERIFIED.

**Lists are filtered, not refused.** A user without the view permission gets a shorter or
empty list and no error. `GET /core/applications/` is filtered by policy as well: a superuser
needs `superuser_full_list=true` to see every application (the CLI sends it).

## 3. Pagination, search and filters

- Parameters `page`, `page_size`, `ordering`, `search`. Default page size 20, maximum 100; both
  are tenant settings and a larger value is clamped without a message.
- Answer: `{"pagination": {"next", "previous", "count", "current", "total_pages",
  "start_index", "end_index"}, "results": [...], "autocomplete": {...}}`. `next` is a page
  number, not a URL. The CLI pages until `current` reaches `total_pages`.
- Some lists are bare arrays: `.../used_by/`, `.../types/`, `/outposts/instances/{uuid}/health/`,
  `/events/events/actions/`, `/events/events/volume/`.
- `ordering=-field` for descending is the usual convention and is UNVERIFIED in the schema.
- To find one object by name use the exact-match filter (`slug=`, `name=`, `username=`,
  `identifier=`), not `search=`.

Filters by list, passed with `--filter key=value`:

| List | Filters |
|---|---|
| Applications | `name`, `slug`, `group`, `for_user` (pk: applications that user can open), `superuser_full_list` |
| Providers (all) | `application__isnull` (`true` lists providers no application uses), `backchannel` |
| Groups | `name`, `is_superuser`, `members_by_username`, `members_by_pk`, `include_users` |
| Users | `username`, `email`, `name`, `is_active`, `is_superuser`, `groups_by_name`, `last_login__lt`, `last_login__isnull` |
| Bindings | `target`, `policy`, `enabled`, `order`, `policy__isnull` |
| Policies | `bindings__isnull` (`true` lists policies bound to nothing) |
| Flows | `slug`, `name`, `designation` (`authentication`, `authorization`, `invalidation`, `enrollment`, `unenrollment`, `recovery`, `stage_configuration`) |
| Outposts | `name__iexact`, `name__icontains`, `managed__icontains`, `providers__isnull` |
| Events | `action`, `username`, `client_ip`, `context_authorized_app`, `context_model_name`, `context_model_pk`, `brand_name` |

## 4. Identifiers

| Object | Addressed by | Note |
|---|---|---|
| Application | `slug` | `pbm_uuid` is what a binding's `target` points at |
| Flow | `slug` | Other objects refer to a flow by its UUID (`pk`), for example a provider's `authorization_flow` |
| Provider, user | integer | |
| Token | `identifier` (a name) | |
| Group, binding, policy, outpost, event, mapping, role, brand | UUID | |

## 5. Routes

Each collection has `GET` (list), `POST` (create), and on one item `GET`, `PUT`, `PATCH`,
`DELETE` and `GET .../used_by/`, unless noted.

| CLI group | Path | Notes |
|---|---|---|
| `info` | `/core/users/me/`, `/admin/version/` | Any valid token |
| `apps` | `/core/applications/` | `GET .../{slug}/check_access/?for_user=<pk>` |
| `apps create-with-provider` | `PUT /core/transactional/applications/` | Create only |
| `providers` | `/providers/all/` (list, get, delete only), `/providers/<type>/` | Types: `oauth2`, `proxy`, `ldap`, `saml`, `radius`, `scim`, `rac`, ... ; `GET /providers/all/types/` lists them |
| `providers setup-urls` | `GET /providers/oauth2/{id}/setup_urls/` | Issuer, discovery, authorize, token, userinfo, JWKS addresses |
| `groups` | `/core/groups/` | `POST .../{uuid}/add_user/` and `/remove_user/` with `{"pk": <user pk>}`, answer 204 |
| `users` | `/core/users/` | Read only in the CLI |
| `bindings` | `/policies/bindings/` | |
| `policies` | `/policies/all/` (list, get, delete only), `/policies/<type>/` | `POST /policies/all/{uuid}/test/` with `{"user": <pk>}` tests one policy |
| `flows` | `/flows/instances/` | Stage bindings: `/flows/bindings/?target=<flow uuid>` |
| `outposts` | `/outposts/instances/` | `GET .../{uuid}/health/` |
| `events` | `/events/events/` | Also `.../volume/?history_days=N`, `.../top_per_user/?action=&top_n=` |
| `mappings` | `/propertymappings/provider/scope/` | The scope mappings an OAuth2 provider lists in `property_mappings` |
| `tokens` | `/core/tokens/` | A token object never contains its key |

Reachable with `raw` and not wrapped: `/admin/system/`, `/tasks/workers/` (there is no
`/admin/workers/` in this version), `/core/brands/`, `/rbac/roles/`, `/rbac/permissions/`,
`/crypto/certificatekeypairs/`, `/sources/all/`, `/outposts/service_connections/all/`.

## 6. Applications, bindings and the engine mode

- `policy_engine_mode` is `any` or `all`, default `any`. It lives on the **target**
  (application, flow, flow stage binding, source), not on the binding.
- `any`: the target passes when any enabled binding passes. `all`: only when every enabled
  binding passes. No enabled binding: the target passes.
- Bindings are evaluated in ascending `order`.
- A binding names exactly one of `policy` (UUID), `group` (UUID) or `user` (pk). Whether the
  server enforces "exactly one" is UNVERIFIED.
- `GET /core/applications/{slug}/check_access/?for_user=<pk>` answers `{"passing": bool,
  "messages": [...], "log_messages": [...]}` and bypasses the policy cache. `for_user` and
  `log_messages` work for superusers only; for anyone else `for_user` is ignored without an
  error and the check runs for the token's own user.
- An application's own bindings: `GET /policies/bindings/?target=<pbm_uuid>&ordering=order`.
- An application with `provider: null` is only a link on the user's page.

## 7. Providers

- A provider serves one application; the link is the application's `provider` field. A
  provider with an empty `assigned_application_slug` is used by nothing and cannot log anyone in.
- `/providers/all/` shows every type with `verbose_name`, `meta_model_name` and the assigned
  application. Writes go to the typed path, hence `--type`.
- OAuth2 model defaults when a field is left out: `client_type` `confidential`, a generated
  128-character `client_secret`, `access_token_validity` `hours=1`, `refresh_token_validity`
  `days=30`, `sub_mode` `hashed_user_id`, `issuer_mode` `per_provider`. How `client_id` is
  generated is UNVERIFIED.
- The API does not obviously add the scope mappings the web wizard preselects (UNVERIFIED), so
  pass `property_mappings` explicitly: the UUIDs of the `openid`, `email` and `profile` rows
  from `mappings list`.
- Without a `signing_key` (a certificate key pair UUID) ID tokens are signed with the client
  secret instead of a key pair (UNVERIFIED here; check what the application expects).

## 8. Outposts

- Types `proxy`, `ldap`, `radius`, `rac`. A provider of one of those types works only while an
  outpost lists its id in `providers`.
- The embedded outpost runs inside the server and is an outpost row with a `managed` value. A
  new proxy provider is not obviously added to it automatically (UNVERIFIED): check.
- `GET .../health/` returns one entry per connected instance: `hostname`, `version`,
  `version_should`, `version_outdated`, `last_seen`. An empty list is read here as "no instance
  connected" (UNVERIFIED).
- `token_identifier` is the name of the outpost's own token, not a secret.

## 9. Events

- An event: `pk`, `user` (who), `action`, `app` (the emitting module), `context` (free-form,
  depends on the action), `client_ip`, `created`, `brand`. Retention is 365 days by default.
- There is no date filter: order by `-created` and stop paging (`events list --limit N`).
- Actions that matter for logins:

| Action | Meaning |
|---|---|
| `login`, `login_failed`, `logout` | A login succeeded, failed, ended |
| `authorize_application` | A user was let into an application (`--filter context_authorized_app=<name>`) |
| `policy_execution` | A policy ran. Only logged for policies with execution logging on |
| `policy_exception`, `property_mapping_exception` | A policy or mapping raised an error; `failure_result` then decides |
| `configuration_error`, `configuration_warning` | For example during the authorization of an application |
| `suspicious_request` | For example a revoked token was used |
| `model_created`, `model_updated`, `model_deleted` | Someone changed configuration (`context_model_name`, `context_model_pk`) |
| `secret_view`, `secret_rotate` | A token key or certificate was read or rotated |
| `impersonation_started`, `impersonation_ended`, `password_set`, `user_write` | Account changes |

**A plain denial by a group or policy binding is not an event** unless that policy logs its
executions. An empty event log does not mean access was granted; `apps access` gives the
verdict.

## 10. Request bodies

Use `patch` (all fields optional) for single changes; `update` is a PUT and needs the full
required set.

- **Application**: `name`, `slug` (required); `provider` (integer or null),
  `policy_engine_mode`, `group` (a label on the user's page), `meta_launch_url`,
  `meta_description`, `meta_publisher`, `open_in_new_tab`, `meta_hide`, `backchannel_providers`.
- **OAuth2 provider**: `name`, `authorization_flow` (flow UUID), `invalidation_flow` (flow
  UUID), `redirect_uris` (array of `{"matching_mode": "strict"|"regex", "url": "..."}`; may be
  empty) are required. Optional: `client_type`, `client_id`, `client_secret`,
  `property_mappings`, `signing_key`, `sub_mode`, `issuer_mode`, the validity fields,
  `grant_types`.
- **Group**: `name` (required); `is_superuser`, `parents`, `users` (pks), `attributes`, `roles`.
- **Binding**: `target` (the target's `pbm_uuid`) and `order` (required); one of `policy`,
  `group`, `user`; `enabled` (default true), `negate` (default false), `timeout` (seconds,
  default 30), `failure_result` (default false: an erroring policy denies).
- **Application with provider**, `PUT /core/transactional/applications/`:

  ```json
  {
    "app": {"name": "Wiki", "slug": "wiki"},
    "provider_model": "authentik_providers_oauth2.oauth2provider",
    "provider": {"name": "Wiki", "authorization_flow": "<uuid>", "invalidation_flow": "<uuid>",
                 "redirect_uris": [{"matching_mode": "strict", "url": "https://wiki.example.com/callback"}],
                 "property_mappings": ["<uuid>", "<uuid>", "<uuid>"]},
    "policy_bindings": [{"group": "<group uuid>", "order": 0}]
  }
  ```

  It creates everything or nothing, and only creates: an existing slug or provider name fails.
  The answer is `{"applied": bool, "logs": [...]}` with HTTP 200 either way; the CLI exits with
  an error when `applied` is false. It does not return the new objects: read
  `apps get <slug>` afterwards (`provider` is the new provider's id). Other `provider_model`
  values follow the pattern `authentik_providers_<type>.<type>provider` (`proxy`, `ldap`,
  `saml`, `radius`, `scim`, `rac`).

## 11. Fields that hold credentials

The CLI redacts these unless `--show-secrets` is given.

| Where | Field |
|---|---|
| OAuth2 provider, in every list and detail answer | `client_secret` |
| Proxy outpost configuration | `client_secret`, `cookie_secret` |
| RADIUS provider | `shared_secret` |
| `GET /core/tokens/{identifier}/view_key/` | `key` |
| `POST /core/users/service_account/` | `token` |
| `POST /core/users/{id}/recovery/` | `link` (whoever holds it takes over the account) |
| `GET /crypto/certificatekeypairs/{uuid}/view_private_key/` | `data` |
| Kubernetes service connection | `kubeconfig` |
| Google Workspace provider and similar | `credentials` |
| `/admin/system/` | `http_headers` echoes the request environment (`HTTP_AUTHORIZATION`, `HTTP_COOKIE`, ...); masked whole |
| Duo and SMS authenticator stages | `admin_integration_key`, `auth` |
| Notification transport | `webhook_url` |

`client_id`, `token_identifier`, `signing_key` and the flow fields are names or references, not
secrets. Free-form objects (`attributes` on users and groups, an event's `context`) can hold
anything an operator put there.

## 12. Traps

1. A rejected token is a 403, not a 401 (section 2).
2. Lists are filtered by what the user may view (section 2).
3. API tokens expire after a day unless created with `expiring: false` (section 1).
4. `for_user` is ignored for non-superusers (section 6).
5. Flows are named by slug in the address and by UUID in bodies (section 4).
6. The transactional create answers 200 even when it applied nothing (section 10).
7. A non-empty `managed` field marks an object that authentik or a blueprint owns. The API
   lets you change it, and a later update or the next blueprint run (hourly) overwrites the
   change. Default flows and the `authentik Read-only` group revert the same way although they
   carry no `managed` field.
8. `PUT` resets what it does not carry; `PATCH` does not.
9. What a `DELETE` takes with it is UNVERIFIED per object: read `used-by` first.
10. Impersonation, user switching and flow execution are browser features; never call them
    with a token.
