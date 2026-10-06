# UniFi Site Manager API (cloud, api.ui.com)

Read-mostly overview API for everything attached to a UI account. Useful when the local
console is unreachable, for ISP/WAN quality history, and for a cross-console inventory.

## Auth and limits

- Key: unifi.ui.com → (account) → **API** → Create API Key. Env: `UNIFI_CLOUD_API_KEY`.
- Header: `X-API-KEY: <key>`, `Accept: application/json`. TLS is real, verification stays on.
- Rate limit: 10,000 requests/min on v1 (100/min on `/ea/`). On 429 `unifi.py` honours
  `Retry-After` and retries up to twice.
- Pagination: `pageSize` + `nextToken`; `unifi.py cloud ...` follows `nextToken`.

Envelope: `{"data": ..., "httpStatusCode": 200, "traceId": "...", "nextToken": "..."}`

## Endpoints

| Path | `unifi.py` | Notes |
|---|---|---|
| `GET /v1/hosts` | `cloud hosts` | consoles (UDM, UCG, Cloud Key, UniFi OS Server). `reportedState.state`, `.version`, `.controllers[]` (network/protect/... versions) |
| `GET /v1/hosts/{id}` | `cloud hosts <id>` | |
| `GET /v1/sites` | `cloud sites` | per-site `statistics.counts` (devices, offline, clients, guests, criticalNotification), `percentages.wanUptime`, `ispInfo`, `internetIssues[]` |
| `GET /v1/devices?hostIds[]=` | `cloud devices [--host-ids ...]` | every adopted device grouped by host, with `status`, `version`, `firmwareUpdatable` |
| `GET /v1/isp-metrics/{5m\|1h}` | `cloud isp-metrics --interval 1h --duration 24h` | WAN latency, packet loss, throughput, downtime per period. 5m keeps 24 h; 1h keeps 30 d. Alternatively `--begin/--end` RFC3339 |
| `POST /v1/isp-metrics/{type}/query` | `raw` style via cloud not wired; rarely needed | query specific sites |
| `GET /v1/sd-wan-configs`, `/{id}`, `/{id}/status` | `cloud sdwan [id]` | SD-WAN hub/spoke configs |

## Remote access to the local Integration API

api.ui.com can proxy Integration API calls to a console using the **cloud** key:

```
https://api.ui.com/v1/connector/consoles/{hostId}/proxy/network/integration/v1/sites
```

`hostId` is `id` from `cloud hosts`. This is the fallback when Claude is not on the LAN.
`unifi.py` does not route through it automatically (latency and error shapes differ), so
when you need it call it explicitly:

```bash
curl -sS -H "X-API-KEY: $UNIFI_CLOUD_API_KEY" -H "Accept: application/json" \
  "https://api.ui.com/v1/connector/consoles/$HOST_ID/proxy/network/integration/v1/sites"
```

## What to use it for

- Health report: `cloud sites` gives the one-line summary (offline count, WAN uptime,
  ISP, recent internet issues) without walking devices.
- "Was the internet down last night?": `cloud isp-metrics --interval 5m --duration 24h`
  and look for `downtime > 0` or `packetLoss` spikes.
- Firmware sweep across consoles: `cloud devices` and filter `firmwareUpdatable`.
