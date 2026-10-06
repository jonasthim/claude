# SSH on UniFi OS consoles and devices

Enable: UniFi OS → **Settings → Control Plane → Console → SSH** (toggle, set password,
add your public key under Advanced). Devices (APs, switches) use the site-wide
device SSH credentials: Network app → **Settings → System → Advanced → Device SSH**.
`scripts/ssh_diag.sh` only needs key auth to the console as `root`.

Prefer the API for anything it covers; SSH is for the moments the API is blind
(signal per client, kernel/WAN logs, adoption problems, a console whose Network app is down).

## Read-only (safe to run any time)

| Command | Where | Shows |
|---|---|---|
| `ubnt-device-info summary` | console | model, firmware, uptime, IPs |
| `info` | AP / switch | adoption state, inform URL, firmware |
| `ubnt-systool cputemp` / `portstatus` / `cpuload` | console | thermals, physical port link state |
| `ip -br addr`, `ip route`, `cat /etc/resolv.conf` | any | addressing, default route, DNS |
| `cat /proc/sys/net/netfilter/nf_conntrack_count` | console | connection table pressure |
| `grep -Ei 'wan|dhcp|link|carrier' /var/log/messages \| tail -50` | console | WAN flaps, DHCP, port link events |
| `journalctl -u unifi-core -n 50` | console | UniFi OS core service |
| `mca-dump` | AP | full JSON state; `.vap_table[].sta_table[]` has per-client `rssi`, `signal`, `tx_rate`, `rx_rate`, `idletime` |
| `mca-dump \| grep -A3 '"mac": "<client mac>"'` | AP | quick signal lookup for one client |
| `iwconfig` / `iw dev` | AP | radios, channels, tx power |
| `swctrl port show` | switch | per-port link, speed, PoE state |
| `swctrl poe show` | switch | PoE draw per port |
| `mca-cli-op info` | AP / switch | same as `info` on newer firmware |

`ssh_diag.sh <host> [system|net|logs]` bundles the console-side set.

## State-changing (only after the plan → confirm protocol)

| Command | Effect | Prefer instead |
|---|---|---|
| `reboot` | reboots the device | `unifi.py devices restart <id>` (goes through the controller, visible in logs) |
| `set-inform http://<controller>:8080/inform` | re-points a device at a controller | adopt via `unifi.py devices adopt --macs` when the device is pending |
| `syswrapper.sh restore-default` | factory reset | never without an explicit, written user request |
| `ubnt-systool reset2defaults` | factory reset the console | same |
| `swctrl poe restart id <port>` | PoE cycle a switch port | `unifi.py devices port-cycle <switch id> --port N` |

Why the preference: controller-driven actions are audited in the UniFi event log and
survive the console's own reboots; raw shell changes are invisible to the controller
and sometimes reverted at the next provision.

## Gotchas

- The console's shell is BusyBox-ish: `free -m`, `df -h`, `ip`, `grep -E` exist; `jq` usually does not.
  Pipe `mca-dump` back to the local machine and parse there.
- Device credentials differ from console credentials. `ssh_diag.sh` targets the console;
  for an AP use `ssh <device-ssh-user>@<ap ip>`.
- Host keys change after firmware updates; `ssh_diag.sh` uses `StrictHostKeyChecking=accept-new`,
  so a *changed* key still fails on purpose. Tell the user rather than disabling the check.
