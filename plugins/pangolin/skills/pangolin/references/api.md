# Pangolin Integration API

Condensed from the fosrl/pangolin source at tag 1.24.0 (`server/routers/integration.ts` and the
handlers it imports) and docs.pangolin.net. Not yet confirmed against a live server; items the
source did not settle are marked UNVERIFIED. The running version's Swagger page at
`<API address>/v1/docs` needs no key and is the truth for that version.

## Contents
1. Enabling and addressing
2. Keys, organizations and actions
3. Envelope and errors
4. Pagination and list filters
5. Identifiers
6. Routes
7. Request bodies
8. Version differences

## 1. Enabling and addressing

- Off by default: `flags.enable_integration_api: true` in `config.yml`. It listens on
  `server.integration_port`, default 3003, usually published as `api.<domain>`.
- Base path `/v1`. `GET /v1/` needs no key and answers `{"message": "Healthy"}` (not the envelope).
- `PANGOLIN_HOST` is the API address, not the dashboard. The CLI adds `/v1`.

## 2. Keys, organizations and actions

- Header: `Authorization: Bearer <apiKeyId>.<secret>`. The key is shown once at creation.
- An **organization key** (dashboard: Organization → API Keys) works in the organizations it is
  linked to. A **root key** (Server Admin → API Keys, self-hosted editions only) reaches every
  organization and the root-only routes.
- Every route checks one **action** (for example `listSites`, `updateResource`). The key's
  actions are chosen when it is created. Root keys need the action as well: root only removes
  the organization check. A read-only key is one that holds only `list*` and `get*` actions.
- Root-only: listing, creating and deleting organizations, all API-key management, identity
  provider writes and `GET /idp/:idpId`, `GET /user/:userId`.
- A key cannot list its own actions (that route is root-only). `pangolin.py access` probes the
  common reads instead.

## 3. Envelope and errors

Success is always `{"data": ..., "success": true, "error": false, "message": "...", "status": 200}`;
the CLI prints `data`. Errors are `{"data": null, "success": false, "error": true, "message":
"...", "status": <code>, "stack": null}`.

| Status | CLI class | Meaning |
|---|---|---|
| 400 | `validation` | The message names the field. Bodies, queries and path parameters are strict objects: an unknown field is rejected |
| 401 | `auth` | `API key required` (no `Bearer` header) or `Invalid API key` |
| 403 | `permission` | `Key does not have permission perform this action` (sic): the action is missing. `Key does not have access to this organization`. `Key does not have root access`. Also plan limits on hosted editions |
| 404 | `not-found` | The object or the route does not exist |
| 409 | `conflict` | Duplicate niceId, domain, subnet, whitelist entry or role assignment |
| 500 | `server` | `An error occurred` |

Creates answer 201, and so do several "set" calls (password, pincode, header auth, whitelist,
roles, users).

## 4. Pagination and list filters

Two styles; the total is at `data.pagination.total` in both, and the schema sets no maximum
page size. The CLI follows whichever the answer carries.

| Style | Parameters | Defaults | Lists |
|---|---|---|---|
| page | `page`, `pageSize` | 1, 20 | sites, resources, roles, users, clients |
| offset | `limit`, `offset` | 1000, 0 | targets, rules, domains, identity providers, organizations, API keys, access tokens, invitations |

The list is under a key named after it: `data.sites`, `data.resources`, `data.targets`,
`data.rules`, `data.domains`, `data.idps`, `data.roles`, `data.orgs`. Key names of other lists
are UNVERIFIED; the CLI takes the one list in the payload.

Filters, passed with `--filter key=value`:

- Sites: `query` (search), `sort_by` (`name|megabytesIn|megabytesOut`), `order` (`asc|desc`),
  `online` (`true|false`), `status` (`pending|approved`), `labels` (comma list).
- Resources: `query`, `sort_by` (`name`), `order`, `enabled` (`true|false`), `authState`
  (`protected|not_protected|none`), `healthStatus` (`healthy|degraded|unhealthy|unknown`),
  `protocol` (`http|https|tcp|udp|ssh|rdp|vnc|inference`), `siteId`, `status`, `labels`.

`query` is a case-insensitive substring match over name, niceId, full domain and labels, so it
filters; it is not a lookup.

## 5. Identifiers

- Numbers: `siteId`, `resourceId`, `targetId`, `ruleId`, `roleId`, `clientId`, `idpId`.
- Strings: `orgId` (the organization's slug), `domainId`, `userId`, `apiKeyId`, `accessTokenId`.
- Sites can be fetched by niceId (`GET /org/:orgId/site/:niceId`). Resources cannot; the CLI
  lists with `query=` and matches niceId, full domain or name exactly.

## 6. Routes

Paths are under `/v1`. **PUT creates, POST updates.** From 1.21 every `/resource...` path also
exists as `/public-resource...`; the un-prefixed paths exist in every version checked.

| Area | Method and path | Action |
|---|---|---|
| Organization | `GET /org/:orgId` | getOrg |
| | `GET /orgs` (root) | listOrgs |
| Sites | `GET /org/:orgId/sites` | listSites |
| | `GET /org/:orgId/site/:niceId`, `GET /site/:siteId` | getSite |
| | `PUT /org/:orgId/site` | createSite |
| | `POST /site/:siteId` | updateSite |
| | `DELETE /site/:siteId` (`?deleteResources=true`) | deleteSite |
| Resources | `GET /org/:orgId/resources`, `GET /site/:siteId/resources` | listResources |
| | `PUT /org/:orgId/resource` | createResource |
| | `GET`, `POST`, `DELETE /resource/:resourceId` | getResource, updateResource, deleteResource |
| Targets | `GET /resource/:resourceId/targets` | listTargets |
| | `PUT /resource/:resourceId/target` | createTarget |
| | `GET`, `POST`, `DELETE /target/:targetId` | getTarget, updateTarget, deleteTarget |
| Rules | `GET /resource/:resourceId/rules` | listResourceRules |
| | `PUT /resource/:resourceId/rule` | createResourceRule |
| | `POST`, `DELETE /resource/:resourceId/rule/:ruleId` | updateResourceRule, deleteResourceRule |
| Resource access | `GET`, `POST /resource/:resourceId/roles` (`{"roleIds": [int]}`, replaces) | listResourceRoles, setResourceRoles |
| | `GET`, `POST /resource/:resourceId/users` (`{"userIds": [string]}`, replaces) | listResourceUsers, setResourceUsers |
| | `POST /resource/:resourceId/roles/add`, `/roles/remove` (`{"roleId": int}`), `/users/add`, `/users/remove` (`{"userId": string}`) | setResourceRoles, setResourceUsers |
| | `POST /resource/:resourceId/password`, `/pincode`, `/header-auth` | setResourcePassword, setResourcePincode, setResourceHeaderAuth |
| | `GET`, `POST /resource/:resourceId/whitelist`, `POST .../whitelist/add`, `/remove` | getResourceWhitelist, setResourceWhitelist |
| | `POST /resource/:resourceId/access-token`, `GET .../access-tokens`, `DELETE /access-token/:id` | generateAccessToken, listAccessTokens, deleteAcessToken (sic) |
| Domains | `GET /org/:orgId/domains`, `GET /org/:orgId/domain/:domainId`, `GET .../dns-records` | listOrgDomains, getDomain, getDNSRecords |
| Identity providers | `GET /idp` | listIdps |
| | `GET /idp/:idpId`, `PUT /idp/oidc`, `POST /idp/:idpId/oidc`, `DELETE /idp/:idpId` (root) | getIdp, createIdp, updateIdp, deleteIdp |
| People | `GET /org/:orgId/users`, `GET /org/:orgId/roles`, `GET /org/:orgId/clients` | listUsers, listRoles, listClients |

There are no health-check routes: a health check is the `hc*` fields of a target. Not listed
here and reachable with `raw`: private (site) resources, resource policies, invitations,
blueprints, request logs, clients, and the AI gateway routes added in 1.22.

## 7. Request bodies

`?` marks an optional field. An update body needs at least one field.

### Site

- Create: `name` (1-255), `type` (`newt|wireguard|local`), `exitNodeId?`, `niceId?`, `pubKey?`,
  `subnet?`, `newtId?`, `secret?`, `address?`. Tunnelled sites need `subnet` and `exitNodeId`
  (from `GET /org/:orgId/pick-site-defaults`); wireguard needs `pubKey`. The answer carries
  `newtId` and `secret` for a Newt site, once.
- Update: `name?`, `niceId?`, `dockerSocketEnabled?`, `autoUpdateEnabled?`, `autoUpdateOverrideOrg?`.
- Fields worth reading: `online`, `lastPing`, `type`, `address`, `megabytesIn`, `megabytesOut`, `status`.

### Resource

- Create, HTTP family: `name`, `domainId` (string), `subdomain?` (string or null), `mode?`
  (`http|ssh|rdp|vnc|tcp|udp|inference`), `stickySession?`, `postAuthPath?`. Without `mode`
  the API answers 400 `mode is required when deprecated fields are not provided`.
- Create, raw TCP or UDP: `name`, `proxyPort` (1-65535), `mode` (`tcp|udp`). Refused unless the
  server has `flags.allow_raw_resources`.
- Update, HTTP family: `name?`, `niceId?`, `subdomain?`, `domainId?`, `ssl?`, `sso?`,
  `blockAccess?`, `emailWhitelistEnabled?`, `applyRules?`, `enabled?`, `stickySession?`,
  `tlsServerName?`, `setHostHeader?`, `skipToIdpId?` (int or null), `requestHeaders?` and
  `responseHeaders?` (arrays of `{name, value}` or null), `maintenanceModeEnabled?`,
  `maintenanceModeType?` (`forced|automatic`), `maintenanceTitle?`, `maintenanceMessage?`,
  `postAuthPath?`, `resourcePolicyId?`. The mode cannot be changed.
- Update, raw: `name?`, `niceId?`, `proxyPort?`, `stickySession?`, `enabled?`, `proxyProtocol?`,
  `proxyProtocolVersion?`.
- List items carry `passwordId`, `pincodeId` and `headerAuthId` (non-null means that method is
  set), `sso`, `whitelist`, `health`, `targets[]` (`targetId, ip, port, enabled, healthStatus,
  siteName`) and `sites[]` (`siteId, siteName, siteNiceId, online`).
- Database defaults: `ssl` false, `blockAccess` false, `enabled` true, `stickySession` false.
  The default of `sso` on a new resource is UNVERIFIED: read the resource back after creating it.

### Target

- Create: `siteId`, `ip`, `port` (1-65535), `enabled?` (default true), `method?` (`http`,
  `https`, at most 10 characters), `mode?` (`http|tcp|udp|ssh|rdp|vnc`), `path?`,
  `pathMatchType?` (`exact|prefix|regex`), `rewritePath?`, `rewritePathType?`
  (`exact|prefix|regex|stripPrefix`), `priority?` (1-1000).
- Health check on the same body: `hcEnabled?`, `hcHostname?`, `hcPort?`, `hcPath?`, `hcScheme?`,
  `hcMode?`, `hcMethod?`, `hcStatus?`, `hcInterval?`, `hcUnhealthyInterval?`, `hcTimeout?`,
  `hcHealthyThreshold?`, `hcUnhealthyThreshold?`, `hcFollowRedirects?`, `hcTlsServerName?`,
  `hcHeaders?` (array of `{name, value}`). **`hcHostname` is required when `hcEnabled` is true.**
  The check has its own address (`hcHostname`, `hcPort`), stored separately from `ip` and `port`,
  so the two can drift apart when a backend moves.
- Update: the same fields, and **`siteId` and `ip` are required every time**.
- Read-only `hcHealth` on answers: `healthy`, `unhealthy` or `unknown`. It is reset to `unknown`
  when the check is disabled or the site is not a Newt site, and starts as `unhealthy` right
  after a check is enabled.

### Rule

- Create: `action` (`ACCEPT|DROP|PASS`), `match`
  (`CIDR|IP|PATH|COUNTRY|COUNTRY_IS_NOT|ASN|REGION|METHOD`), `value`, `priority` (int),
  `enabled?`. HTTP resources only.
- Update: the same, all optional except **`priority`, which is required every time**.
- Rules apply only while the resource has `applyRules: true`.

### Resource access

- Password `{"password": "<4-100 chars>" | null}`, pincode `{"pincode": "<6 digits>" | null}`;
  null clears. Header auth `{"user": ..., "password": ..., "extendedCompatibility": ...}`, all
  three keys required, each nullable.
- Whitelist `{"emails": ["a@example.com", "*@example.com"]}`; 400 `Email whitelist is not
  enabled for this resource` unless `emailWhitelistEnabled` is true.
- Access token `{validForSeconds?, title?, path?, description?, persistSession?, userId?}`; the
  answer carries the secret `accessToken`.
- The admin role cannot be assigned to a resource (400).

## 8. Version differences

| Version | Notes |
|---|---|
| 1.18 | No resource policies, no `/public-resource` aliases |
| 1.21 | Resource policies and the `public-`/`private-` aliases appear |
| 1.22 and later | AI gateway routes appear |
| 1.24 | The version this page describes |

1.19 and 1.20 were not compared.
