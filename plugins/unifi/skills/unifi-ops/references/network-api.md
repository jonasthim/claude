# UniFi Network Integration API (local) — cheat sheet

The official, API-key based API served by the Network application itself. Everything
`scripts/unifi.py` does (except `cloud` and SSH) goes through it.

## Contents
1. Connection and auth
2. Response envelope, pagination, filters
3. Endpoint map (what `unifi.py` verb calls what)
4. Payload examples for writes
5. Error codes and how to react
6. Observed on a live console
7. Known gaps

## 1. Connection and auth

| | UniFi OS console (UDM, UDM Pro/SE, UCG, UDR, Cloud Key G2+) | Self-hosted Network app |
|---|---|---|
| Base | `https://<host>/proxy/network/integration/v1` | `https://<host>:8443/integration/v1` (varies) |
| Auth header | `X-API-Key: <key>` | same |
| TLS | self-signed by default; `unifi.py` skips verification unless `UNIFI_VERIFY_TLS=1` | same |

Create a key in the Network app: **Settings → Control Plane → Integrations → API Keys → Create**
(older 9.x builds: Settings → Integrations). Keys inherit the creating admin's role; a
read-only admin's key cannot write.

`unifi.py` probes `/proxy/network/integration/v1/info` first and falls back to
`/integration/v1/info`, then caches the base for the run.

## 2. Envelope, pagination, filters

List responses:
```json
{"offset": 0, "limit": 25, "count": 25, "totalCount": 137, "data": [ ... ]}
```
`limit` max is 200. `unifi.py` follows pages automatically and returns the bare `data`
array; pass `--limit N` to stop early.

Filter query parameter (`--filter`), syntax `<property>.<function>(<args>)`:

| Function | Example |
|---|---|
| `eq`, `ne` | `state.eq('OFFLINE')`, `enabled.ne(true)` |
| `like` (wildcard `*`) | `name.like('Office*')` |
| `in`, `notIn` | `type.in('WIRELESS','WIRED')` |
| `gt`, `ge`, `lt`, `le` | `vlanId.gt(1)` |
| `isNull`, `isNotNull`, `isEmpty` | `name.isNotNull()` |
| `contains`, `containsAny`, `containsAll` | `networkIds.contains('<id>')` |
| combinators | `and(enabled.eq(true), name.like('IoT*'))`, `or(...)`, `not(...)` |

Strings are single-quoted. Filtering is server-side and exact on property names, so for
"find the laptop called something like jonas" use `clients find`, which matches
case-insensitively across name, IP and MAC on the client side.

## 3. Endpoint map

All site-scoped paths are `/v1/sites/{siteId}/...`. `unifi.py` resolves `{siteId}` from
`UNIFI_SITE` (name, `internalReference`, or id) via `GET /v1/sites`.

| Area | Method + path | `unifi.py` |
|---|---|---|
| App info | `GET /v1/info` | `info` |
| Sites | `GET /v1/sites` | `sites list` |
| Devices | `GET devices`, `GET devices/{id}`, `GET devices/{id}/statistics/latest` | `devices list/get/stats` |
| Device actions | `POST devices/{id}/actions` body `{"action":"RESTART"}` (10.6.106: RESTART is the only value) | `devices restart` / `devices action --action X` |
| Port actions | `POST devices/{id}/interfaces/ports/{portIdx}/actions` body `{"action":"POWER_CYCLE"}` | `devices port-cycle/port-enable/port-disable --port N` |
| Unadopt | `DELETE devices/{id}` | `devices unadopt` |
| Pending devices | `GET /v1/pending-devices`, `POST /v1/pending-devices` `{"macAddresses":[...]}` | `devices pending/adopt --macs` |
| Clients | `GET clients`, `GET clients/{id}` | `clients list/get/find` |
| Client actions | `POST clients/{id}/actions` `{"action":"AUTHORIZE_GUEST_ACCESS", "timeLimitMinutes":60}` | `clients authorize/block/unblock/action` |
| Networks (VLANs) | `GET/POST networks`, `GET/PUT/DELETE networks/{id}`, `GET networks/{id}/references` | `networks ...` |
| WiFi (SSIDs) | `GET/POST wifi/broadcasts`, `GET/PUT/DELETE wifi/broadcasts/{id}` | `wifi ...` |
| Firewall zones | `GET/POST firewall/zones`, `GET/PUT/DELETE firewall/zones/{id}` | `firewall zones ...` |
| Firewall policies | `GET/POST firewall/policies`, `GET/PUT/PATCH/DELETE firewall/policies/{id}`, `GET/PUT firewall/policies/ordering` | `firewall policies ...` |
| ACL rules | `acl-rules`, `acl-rules/{id}`, `acl-rules/ordering` | `acl ...` |
| Traffic matching lists | `traffic-matching-lists[/{id}]` | `traffic ...` |
| DNS policies | `dns/policies[/{id}]` | `dns ...` |
| Hotspot vouchers | `GET/POST hotspot/vouchers`, `GET/DELETE hotspot/vouchers/{id}`, `DELETE hotspot/vouchers?filter=` | `vouchers ...` |
| WANs | `GET wans` | `wans list` |
| VPN | `GET vpn/site-to-site-tunnels`, `GET vpn/servers` | `vpn tunnels/servers` |
| RADIUS | `GET radius/profiles` | `radius list` |
| Switching | `GET switching/switch-stacks`, `switching/lags`, `switching/mc-lag-domains` | `raw GET ...` |
| DPI catalog | `GET /v1/dpi/categories`, `GET /v1/dpi/applications` | `dpi categories/applications` |
| Countries | `GET /v1/countries` | `raw GET /countries` |
| Anything else | | `raw <METHOD> <path> [--body ...]` (`{siteId}` is substituted) |

Device fields worth knowing: `state` (ONLINE, OFFLINE, PENDING_ADOPTION, UPDATING, ...),
`firmwareUpdatable`, `uplink.deviceId` (details only), `interfaces.ports[]` with
`idx`, `state`, `speedMbps`, `poe`, and `interfaces.radios[]`. Statistics add `uptimeSec`,
`cpuUtilizationPct`, `memoryUtilizationPct`, `uplink.txRateBps/rxRateBps`, and per-radio
`txRetriesPct` (a high value on an AP points at interference or a weak client).

Client fields: `type` (WIRED, WIRELESS, VPN, TELEPORT), `access.type` (DEFAULT, GUEST,
BLOCKED...), `uplinkDeviceId` (the AP or switch it hangs off), `connectedAt`.
`unifi.py` adds `uplinkDeviceName` for convenience.

## 4. Payload examples

Shapes follow the 10.x OpenAPI. Field names move between releases: when the API answers
400 it names the offending field, so read the message and adjust rather than guessing
twice. The exact schema for a given version is at
`https://developer.ui.com/network/v<applicationVersion>/openapi.json`
(`applicationVersion` comes from `unifi.py info`).

**Network (VLAN) with DHCP:**
```json
{
  "name": "Guest 40",
  "enabled": true,
  "vlanId": 40,
  "management": false,
  "ipv4": {
    "gatewayAddress": "10.0.40.1",
    "subnet": "10.0.40.0/24",
    "dhcp": {"mode": "SERVER", "rangeStart": "10.0.40.100", "rangeStop": "10.0.40.250", "leaseTimeSec": 3600}
  },
  "isolation": {"networkIsolationEnabled": true, "internetAccessEnabled": true}
}
```

**WiFi broadcast (SSID) on that network:**
```json
{
  "name": "Thim Guest",
  "enabled": true,
  "networkId": "<network id>",
  "hidden": false,
  "security": {"type": "WPA2_WPA3_PERSONAL", "passphrase": "<at least 8 chars>", "pmfMode": "OPTIONAL"},
  "bands": ["2.4GHz", "5GHz"],
  "broadcastingDeviceIds": ["<ap id>", "<ap id>"],
  "clientIsolationEnabled": true
}
```

**Firewall policy (zone based):**
```json
{
  "name": "Block Guest -> Internal",
  "enabled": true,
  "action": "BLOCK",
  "source": {"zoneId": "<zone id>", "matchingTarget": "ANY"},
  "destination": {"zoneId": "<zone id>", "matchingTarget": "ANY"},
  "ipVersion": "BOTH",
  "protocol": "all",
  "loggingEnabled": false
}
```
Narrow a policy with `"matchingTarget": "IP"` plus `"ipAddresses": [...]` and a `"port"`
string, or `"matchingTarget": "NETWORK"` plus `"networkIds": [...]`. `PATCH` changes one
field (for example `{"enabled": false}`) without having to resend the whole object.

**Reorder policies:** `PUT firewall/policies/ordering` with `{"policyIds": ["<id>", ...]}`
listing every user-defined policy in the desired order. Always `GET` the current order
first and move only the id you mean to move.

**Device / port / client actions:** `{"action": "RESTART"}`, `{"action": "POWER_CYCLE"}`,
`{"action": "AUTHORIZE_GUEST_ACCESS", "timeLimitMinutes": 120}`. If an action name is not
accepted, the 400 message lists the allowed values for that build; use
`devices action --action <NAME>` / `clients action --action <NAME>` with one of those.

## 5. Errors

```json
{"code": "RESOURCE_NOT_FOUND", "status": "NOT_FOUND", "message": "..."}
```
| Status | Meaning | Do |
|---|---|---|
| 400 | body/filter invalid | read `message`, fix the field it names |
| 401 / 403 | key missing, wrong, or lacks role | check `UNIFI_API_KEY`; create the key as a full admin for writes |
| 404 | wrong base path or id | `unifi.py` already probes both base paths; re-list to find the id |
| 409 | conflict (duplicate name/VLAN) | list first, reuse or rename |
| 429 | rate limit | wait, retry once |
| 5xx | controller busy/provisioning | wait 30 s, retry once; if it persists check the console UI |

`unifi.py` exit codes: 1 usage, 2 API error, 3 "needs --yes".

## 6. Observed on a live console (Network 10.6.106, UCG Fiber)

- `/devices/{id}/actions` accepts only `RESTART`. `LOCATE` returns
  `400 api.request.unknown-type-id "Invalid $.action value 'LOCATE' (valid values: 'RESTART')"`.
  The dry run cannot detect this; only the controller's 400 can.
- List endpoints are summaries: `wifi/broadcasts` lists carry `id`, `name`, `enabled` only;
  `wans` lists carry `id`, `name`; device list items have no `uplink`. Use `get <id>` for full objects.
- `firewall/policies[].action` is an object (`{"type": "ALLOW", "allowReturnTraffic": true}`), not a string.
- Per-device `statistics/latest` returned `uptimeSec`, `cpuUtilizationPct`, `memoryUtilizationPct`
  and per-radio `txRetriesPct` for every online device.
- **Two id spaces.** The Integration API uses UUIDs for networks, policies and devices. The
  v2 UI API (`/proxy/network/v2/api/site/<site>/...`) and the legacy REST API
  (`/proxy/network/api/s/<site>/rest/...`) use 24-hex Mongo-style ids for the same objects. They
  are not interchangeable; a network is `31017aa0-...` in one and `69c657ca...` in the other.
  Match objects by name or MAC when crossing APIs, never by id.
- **Version fields differ.** The gateway's `firmwareVersion` in `devices list` (Network app's
  view of the console) and `cloud hosts` → `reportedState.version` (UniFi OS as seen by the
  cloud) can disagree. `info` → `applicationVersion` is the Network application; say which one
  you are quoting.

## 7. Known gaps (as of 10.x)

- No client → switch-port mapping: wired clients expose `uplinkDeviceId` only, and switch
  `interfaces.ports[]` has no MAC table. Ask the user or read it over SSH (`mca-dump` on the switch).
- No firmware upgrade action in the documented device actions; firmware updates go through the UI
  (or `devices action --action UPGRADE --dry-run` to see whether this build accepts it).
- No per-client RSSI/signal, no historical stats, no event log. For "why does X drop",
  combine AP `txRetriesPct` from `devices stats`, `connectedAt` churn from repeated
  `clients find`, and (if SSH is available) `mca-dump` on the AP (see `ssh-commands.md`).
- Port forwarding, traffic routes, and DHCP reservations are not exposed as first-class
  resources; `raw` the newest OpenAPI if the build has them, otherwise point the user to the UI.
- Site creation/deletion, admin users and backups are not available.
