# Playbooks

Step-by-step flows for the four jobs this skill is used for most. `U` below means
`python3 "${CLAUDE_PLUGIN_ROOT}/skills/unifi-ops/scripts/unifi.py"`.

## Contents
1. Client troubleshooting ("why is X offline / slow / dropping")
2. Inventory and health report
3. Configuration change (network, SSID, firewall, ACL, DNS)
4. Device and client actions
5. Health report template

---

## 1. Client troubleshooting

Goal: a diagnosis the user can act on in under a minute of reading, not a data dump.

1. **Find the client.** `U clients find <name|ip|mac> --table`. Zero hits → the client is
   not currently associated. Say so, then check whether it was wired or wireless last time
   (ask) and jump to the matching branch below with the AP/switch it would use.
   Several hits → show the table and continue with the one that matches best; mention the others.
2. **Look at what it hangs off.** The `uplinkDeviceName` column names the AP or switch.
   `U devices get <uplinkDeviceId>` (state, uplink, ports/radios) and
   `U devices stats <uplinkDeviceId>` (uptime, CPU/mem, `txRetriesPct` per radio).
3. **Branch.**
   - *Wireless:* `U report health --table` gives every AP's uptime, CPU/mem and per-radio
     `txRetriesPct` in one call, which is the comparison you want. An AP with `uptimeSec` of minutes has just rebooted (power? firmware?);
     `txRetriesPct` above ~15–20% on the client's band means interference or a weak link
     (far from the AP, 2.4 GHz congestion); CPU/mem above ~80% suggests an overloaded AP.
     Compare with the other APs: if one AP looks bad and the others fine, the AP is the story.
     Check `connectedAt`: a very recent value on a device that "should" have been on all day
     means it keeps re-associating. If signal numbers are needed, `mca-dump` on that AP via SSH
     (see `ssh-commands.md`).
   - *Wired:* find the switch port: in `devices get <switch>` look at `interfaces.ports[]`
     for a port whose `state` is UP with the right speed. A `speedMbps` of 100 on a gigabit
     device points at a bad cable; `poe.state` DOWN on a PoE camera means no power. An
     OFFLINE switch upstream (compare `uplink.deviceId` chain) explains every client behind it.
   - *VPN:* check `U vpn servers` is enabled and `U wans list` shows the WAN up.
4. **Check the network it is on.** Does the client's IP fall in the expected network's subnet
   (`U networks list --table`)? A guest IP on a device that should be on the LAN points at
   the wrong SSID or a VLAN/port profile change. Is there a firewall policy blocking the path it
   needs (`U firewall policies list --table`, then `firewall policies ordering`)?
5. **Widen if nothing explains it.** `U report health --no-stats` for site-wide problems
   (several devices OFFLINE, gateway just rebooted), `U cloud isp-metrics --interval 5m --duration 24h`
   for WAN loss, `ssh_diag.sh <gateway> logs` for link flaps and DHCP errors.
6. **Answer.** Lead with the most likely cause in one sentence, then the evidence (two to
   four bullets with the numbers that matter), then the fix. If the fix is an action
   (restart AP, cycle port) offer it and go through the change protocol in §4.

## 2. Inventory and health report

1. `U report health --table` — devices with state, firmware, uptime, CPU/mem; WANs; client
   counts. Use `--no-stats` first on very large sites, then fetch stats only for the devices that
   look wrong.
2. `U cloud sites --table` (if `UNIFI_CLOUD_API_KEY` is set) — WAN uptime %, ISP, recent
   internet issues, critical notification count.
3. Optional depth: `U devices pending` (unadopted gear), `U wifi list --table`,
   `U networks list --table`, `U firewall policies list --table`.
4. Write the report using the template in §5. Flag, in this order: OFFLINE devices,
   devices with `firmwareUpdatable`, uptime under 1 h (unexpected reboots), CPU or memory above 80%,
   WAN downtime or loss in the last 24 h, pending adoptions. If nothing is wrong, say so plainly
   in one line and keep the tables short.

## 3. Configuration change

Applies to networks, wifi, firewall zones/policies, acl, dns, traffic lists, vouchers,
and to `raw` with any method other than GET.

1. **Read before you write.** List the collection the change touches. For an update, `get`
   the object and build the new body from the current one so you do not drop fields the
   UI set (PUT replaces the whole object; use `patch` for firewall policies when changing one field).
2. **Resolve ids, never guess them.** Network ids for SSIDs, zone ids for policies, AP ids
   for `broadcastingDeviceIds`.
3. **Check blast radius.**
   - Deleting or re-addressing a network: `U networks references <id>` lists SSIDs, zones
     and policies that depend on it. Mention every dependent object in the plan.
   - Firewall: `U firewall policies ordering`. A new BLOCK that lands above an existing ALLOW
     changes behaviour for everything the ALLOW covered.
   - Anything touching the network marked `management: true`, the SSID or VLAN the user is
     on right now, or the zone/policy carrying Claude's own API path can cut the session off
     mid-change. Call that out explicitly and prefer doing those last, one at a time.
4. **Dry run and present the plan.** Run the write with `--dry-run`. Show the user: what
   changes (in words), the exact body, the dependent objects, and what to verify afterwards.
   Ask for confirmation. Do not bundle several changes into one confirmation; a VLAN plus its
   SSID plus a firewall policy is three confirmations (or one explicit "yes to all three"),
   because each has its own failure mode.
5. **Apply** with `--yes`, one change at a time. On a 400, read the message, fix the field it
   names, show the corrected body, and re-ask only if the intent changed.
6. **Verify** by re-reading (`get` or `list --filter`), then report what is now in place as
   a short before/after. If verification fails, say so and offer to revert (you have the
   previous object from step 1).

## 4. Device and client actions

Same protocol as §3, shorter plan. Spell out the impact before asking:

| Action | Impact to state |
|---|---|
| `devices restart <gateway>` | whole site loses WAN and the controller for 2–5 min; Claude's API session dies |
| `devices restart <switch>` | every client and AP behind it drops for ~1–2 min |
| `devices restart <ap>` | its wireless clients roam or drop for ~1 min |
| `devices port-cycle <switch> --port N` | that port loses link/PoE ~10 s; if N is an uplink, everything downstream drops |
| `clients block <id>` | device loses all network access until unblocked |
| `clients authorize <id> --minutes` | grants guest portal access |
| `devices adopt --macs` | adopts pending devices; they reprovision |
| `devices unadopt <id>` | factory-resets the device's config; avoid unless asked explicitly |

Verified against Network 10.6.106: `RESTART` is the only accepted device action (no LOCATE). Port
actions (`POWER_CYCLE`, `ENABLE`, `DISABLE`) and client actions (`BLOCK`, `UNBLOCK`,
`AUTHORIZE_GUEST_ACCESS`) match the published API but have not been exercised live yet; if the
controller answers 400 with "valid values: ...", relay those values to the user instead of guessing.

Before a port cycle you need the port index, and the API does not provide a client → port
mapping (`clients` carry `uplinkDeviceId` only; `interfaces.ports[]` has no MAC table). Do not
derive it from which PoE ports are UP: on a switch with several PoE devices that is a guess, and
the wrong guess cuts power to an AP or another camera. Either ask the user (UniFi app →
Clients → the device → Connection shows the port) or, with device SSH, read the switch's MAC
table (`ssh-commands.md`, `mca-dump` `port_table[].mac_table[]`). Then `devices get <switch>`
to confirm that port is UP with PoE before the dry run. Before a restart, confirm the device id
belongs to the device the user named (`devices get` and read back the name/model).

## 5. Health report template

```
## <Site name> health — <date>

**Status:** <one line: all good / N issues>

### Issues
- <device> is OFFLINE since <time or "unknown">; last seen behind <uplink>
- <device> has a firmware update available (<current> → updatable)
- WAN: <downtime/loss summary from isp-metrics, or "no loss in 24 h">

### Devices
| Device | Model | IP | State | Firmware | Uptime | CPU | Mem |
|---|---|---|---|---|---|---|---|

### Clients
<total> clients: <wireless> wireless, <wired> wired, <vpn> VPN, <guest> guest

### Recommended next steps
1. ...
```

Keep it to what changed or needs attention; a healthy site is a five-line report.
