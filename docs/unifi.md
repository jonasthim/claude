# UniFi

Operates a Ubiquiti UniFi network through the official UniFi Network Integration API (local, API key) and the
Site Manager API (api.ui.com), with optional read-only SSH diagnostics: client troubleshooting, inventory and
health reports, networks and VLANs, SSIDs, firewall zones and policies, ACLs, DNS, and device actions. The
bundled CLI (`unifi.py`) is standard-library Python 3.8+.

Needs a UniFi OS console (UDM, UDM Pro/SE, UCG, UDR, Cloud Key G2+) or a self-hosted Network application,
Network 9.4 or newer (10.x for network, SSID and firewall writes), reachable from where Claude Code runs.

Full reference: [plugins/unifi/README.md](https://github.com/jonasthim/claude/blob/main/plugins/unifi/README.md).

## Create the keys

1. **Integration API key**: in the Network app, *Settings → Control Plane → Integrations → API Keys → Create
   API Key*. A read-only admin's key gives a read-only plugin; create it as a full admin to allow changes.
2. **Site Manager key** (optional): at [unifi.ui.com](https://unifi.ui.com) → API, for ISP metrics and a
   multi-console overview. It is a different key from the Integration key.
3. **SSH** (optional): *Settings → Control Plane → Console → SSH*, and add your public key.

## Configure

| Variable | Required | Purpose |
|---|---|---|
| `UNIFI_HOST` | yes | Console IP or hostname |
| `UNIFI_API_KEY` | yes | Integration API key |
| `UNIFI_SITE` | no | Site name, default `default` |
| `UNIFI_CLOUD_API_KEY` | no | Site Manager key (`UNIFI_SITE_MANAGER_API_KEY` also works) |
| `UNIFI_VERIFY_TLS` | no | `1` verifies the console certificate. Off by default because consoles ship self-signed certificates |
| `UNIFI_TIMEOUT` | no | HTTP timeout in seconds, default 20 |
| `UNIFI_CACHE_DIR` | no | Discovery cache, default `~/.cache/unifi-ops` (6 h); `0` disables it |

## What to ask

Start with "check my unifi setup". Then, for example:

- "Why does my laptop keep dropping off wifi?"
- "Give me a health report."
- "What's connected to the office AP?"
- "Create a guest network on VLAN 30 with its own SSID."
- "Restart the hallway AP."

## Try it without a controller

```
export UNIFI_MOCK_DIR=$PWD/plugins/unifi/skills/unifi-ops/evals/fixtures
python3 plugins/unifi/skills/unifi-ops/scripts/unifi.py report health --table
```

The fixtures describe a synthetic home site; writes are echoed back and never stored.

## Safety

- Reads never prompt. Every write prints the exact request and stops unless `--yes` is given; `--dry-run`
  prints it without sending.
- Claude shows one plan per change, calls out the blast radius (dependent SSIDs, zones and policies, uplink
  ports, the management network, the gateway), and verifies afterwards.
- WiFi passphrases, RADIUS secrets and voucher codes are redacted unless `--show-secrets` is passed.
- SSH is read-only in the bundled script.

See also the [safety model](safety.md).

## Not covered

UniFi Protect, Access and Talk; the legacy `/api/s/<site>` controller API; several consoles in one session.
