---
name: unifi-ops
description: Operate a Ubiquiti UniFi network through the official UniFi Network Integration API, the Site Manager cloud API and SSH. Use this whenever the user mentions UniFi, Ubiquiti, a Dream Machine / UDM / UCG / Cloud Gateway / Cloud Key, the UniFi controller or Network application, access points, SSIDs or WiFi networks, VLANs, firewall zones or policies, ACLs, PoE ports, client devices on their network, or asks "why is X offline / slow / dropping", "what's connected", "give me a health report", "block this device", "restart the AP", "create a guest network", or anything else about managing their home or office network gear, even when they don't say "UniFi" but their setup clearly is one. Reads are free; every change goes through a plan → confirm → apply → verify protocol.
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/skills/unifi-ops/scripts/unifi.py *), Bash(${CLAUDE_PLUGIN_ROOT}/skills/unifi-ops/scripts/ssh_diag.sh *)
---

# UniFi operations

You are operating someone's real network. Reading is cheap and safe; writing can knock the
user (and you) offline. The bundled CLI makes the safe path the easy path: it handles auth,
TLS, site resolution and pagination, and it refuses to send a change until `--yes` is given.

```
U="python3 ${CLAUDE_PLUGIN_ROOT}/skills/unifi-ops/scripts/unifi.py"
$U --help            # groups: info sites devices clients networks wifi firewall acl dns traffic vouchers wans vpn radius dpi report raw cloud
$U devices --help    # verbs per group
```

Output is JSON by default; add `--table` to any list for a markdown table that already
includes the `id` column, so one call serves both reading and the follow-up command.
`--help` works everywhere, but this is the whole surface:

```
info                         sites list
devices  list|get|stats|restart|action --action X|port-cycle|port-enable|port-disable --port N|unadopt|pending|adopt --macs
clients  list|get|find <name|ip|mac>|block|unblock|authorize --minutes N|action --action X
networks list|get|references|create|update|delete        wifi     list|get|create|update|delete
firewall zones|policies  list|get|create|update|patch|delete|ordering|reorder
acl      list|get|create|update|delete|ordering|reorder  dns / traffic / vouchers  list|get|create|update|delete
wans list   vpn tunnels|servers   radius list   dpi categories|applications
report health [--no-stats]   raw <METHOD> <path> [--body ...]   cloud hosts|sites|devices|isp-metrics|sdwan
flags: --table  --filter "<expr>"  --limit N  --site NAME  --body <file|-|json>  --dry-run  --yes
```

`report health` already contains per-device uptime, CPU/mem and per-band `txRetriesPct_2g/5g/6g`,
so for "is this AP worse than the others" you do not need a `stats` call per device. List
endpoints return summaries on current firmware (wifi lists carry only name/enabled/id, WANs only
id/name, devices no uplink); `--table` hides columns that came back empty, and `get <id>` has the
full object when you need it. `devices list` and `clients find` carry the uplink device name; the Integration API
does not say which switch *port* a wired client is on, so when a task needs a port, ask the
user or use SSH (`references/ssh-commands.md`) instead of probing endpoints for it.

## First call of a session

Run `$U info`. Three outcomes:

- Version JSON → good, continue.
- "UNIFI_HOST and UNIFI_API_KEY must be set" → the user has not configured the environment.
  Point them at the README's setup section (API key: Network app → Settings → Control Plane →
  Integrations → API Keys) and stop; nothing else will work.
- API key rejected / cannot reach host → say which it is. A 401/403 is the key; a connection
  error means Claude is not on the LAN (VPN, or try the cloud fallback in
  `references/site-manager-api.md`).

Environment the scripts read: `UNIFI_HOST`, `UNIFI_API_KEY`, `UNIFI_SITE` (default `default`),
`UNIFI_VERIFY_TLS` (off by default; consoles have self-signed certs), `UNIFI_CLOUD_API_KEY`
or `UNIFI_SITE_MANAGER_API_KEY` (cloud commands; this is the unifi.ui.com key, and the two keys
are not interchangeable), `UNIFI_MOCK_DIR` (fixtures for offline testing).

## Pick the workflow

Detailed steps live in `references/playbooks.md`; read the matching section before starting.

| User says | Workflow | Start with |
|---|---|---|
| "why is my laptop slow / the camera offline / wifi dropping" | §1 Client troubleshooting | `$U clients find <name\|ip\|mac> --table` |
| "how's the network", "health report", "anything need updating" | §2 Inventory & health | `$U report health --table` |
| "create a guest VLAN", "add an SSID", "block IoT from LAN", "disable that rule" | §3 Configuration change | list the collection, then `--dry-run` |
| "restart the office AP", "power cycle port 7", "block this device" | §4 Device & client actions | `$U devices get <id>` to confirm identity, then `--dry-run` |
| "what's on my network", "list devices / clients / networks" | plain read | `$U <group> list --table` |

API details (endpoints, filter syntax, payload shapes, error meanings) are in
`references/network-api.md`. Cloud/ISP metrics are in `references/site-manager-api.md`.
Shell commands are in `references/ssh-commands.md`.

## The change protocol

Every mutating verb (`create`, `update`, `patch`, `delete`, `reorder`, `restart`,
`port-cycle`, `block`, `adopt`, non-GET `raw`, ...) behaves the same way. A dry run proves the
request shape, not that the controller accepts it: on Network 10.6 the only device action is
`RESTART` (verified live; there is no LOCATE), and port and client actions are documented but
unverified. A 400 names the valid values, so when one comes back, report that rather than retrying
with a guess.

1. Without `--yes` the script prints the exact method, URL and body and exits with code 3.
   That output *is* your plan. Show it to the user in words plus the body, together with what
   depends on the object (`networks references <id>`, `firewall policies ordering`) and what
   the impact is (see the impact table in playbooks §4).
2. Wait for the user to confirm. One confirmation covers one change. If they asked for a
   VLAN, an SSID and a firewall rule, that is three plans; apply them in dependency order
   (network → SSID → policy) and verify each before the next.
3. Re-run with `--yes`. On a 400, the message names the field; fix only that and show the
   corrected body before sending again.
4. Verify by reading the object back and report before/after in two or three lines.

Things that deserve an explicit warning in the plan, because they can end the session or
lock the user out: changes to the network marked `management: true`, to the SSID or VLAN
the user is connected through, to the firewall zone or policy that carries the API path you
are using, restarting the gateway, and power-cycling a port that is a switch or AP uplink.
`PUT` replaces the whole object, so build update bodies from the current `get` output; for
firewall policies prefer `patch` when only one field changes. Never bulk-delete vouchers
without a `--filter` (the script refuses anyway). `devices unadopt` factory-resets a
device; only do it when the user asks for exactly that.

## Answering

Lead with the conclusion, then the evidence as a markdown table or a few bullets with the
numbers that matter (state, uptime, firmware, CPU/mem, `txRetriesPct`, port speed), then the
next step. Hide ids unless the user needs them for a follow-up; keep raw JSON for when they
ask for it. For a health report use the template in playbooks §5. If the API cannot see
something (per-client signal, history, port forwards), say so in one line and offer the SSH
or UI route instead of guessing.

## Testing without a controller

`UNIFI_MOCK_DIR="${CLAUDE_PLUGIN_ROOT}/skills/unifi-ops/evals/fixtures"` makes every
command answer from a synthetic site (gateway, two switches, three APs, 15 clients, three
VLANs, firewall zones/policies). Writes are echoed back, not stored. Use it to demonstrate
a flow or check a body shape before touching the real network.
