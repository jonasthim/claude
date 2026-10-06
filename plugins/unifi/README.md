# claude-unifi-skill

A [Claude Code](https://claude.com/claude-code) plugin for operating Ubiquiti UniFi networks.
It ships one skill, **unifi-ops**, that teaches Claude to:

- troubleshoot clients ("why does my laptop keep dropping off wifi?")
- produce inventory and health reports (devices, firmware, uptime, WAN/ISP quality)
- make guarded configuration changes: networks/VLANs, SSIDs, firewall zones and policies, ACLs, DNS
- run device and client actions: restart, PoE port cycle, block/unblock, adopt

It talks to the **official UniFi Network Integration API** (local, API-key based) and the
**UniFi Site Manager API** (api.ui.com), with optional read-only SSH diagnostics. No legacy
cookie-login API, no third-party dependencies: the bundled CLI is stdlib-only Python 3.

Every change follows a plan → confirm → apply → verify protocol, and the CLI itself refuses to
send a mutating request unless `--yes` is passed, so Claude cannot change your network by accident.

## Requirements

- A UniFi OS console (UDM, UDM Pro/SE, UCG, UDR, Cloud Key G2+) or self-hosted Network
  application, **Network 9.4 or newer** (10.x recommended for network/SSID/firewall writes)
- Claude Code running on a machine that can reach the console (same LAN or VPN)
- `python3` (3.8+); `ssh` only if you want the SSH diagnostics

## Install

If the repository is public:

```
/plugin marketplace add jonasthim/claude-unifi-skill
/plugin install unifi@claude-unifi-skill
```

While the repository is private, the marketplace add over HTTPS has no credential, so clone it
with an authenticated client first and add the local path:

```bash
gh repo clone jonasthim/claude-unifi-skill ~/src/claude-unifi-skill     # or: git clone git@github.com:jonasthim/claude-unifi-skill.git
claude plugin marketplace add ~/src/claude-unifi-skill
claude plugin install unifi@claude-unifi-skill
```

Updating later is `git pull` in that clone followed by `claude plugin update unifi@claude-unifi-skill`.
The scripts also run directly from the clone without installing the plugin:
`python3 ~/src/claude-unifi-skill/plugins/unifi/skills/unifi-ops/scripts/unifi.py info`.

## Configure

1. **Create an Integration API key.** In the UniFi Network app: *Settings → Control Plane →
   Integrations → API Keys → Create API Key*. Create it as a full admin if you want Claude to
   be able to make changes; a read-only admin's key gives you a read-only skill.
2. **(Optional) Create a Site Manager key** at [unifi.ui.com](https://unifi.ui.com) → API, for
   ISP metrics and multi-console overview.
3. **(Optional) Enable SSH** on the console (*Settings → Control Plane → Console → SSH*) and add
   your public key.
4. Export the variables in the shell you start Claude Code from (or put them in a `.env` you source):

```bash
export UNIFI_HOST=192.168.1.1            # console IP or hostname
export UNIFI_API_KEY=...                 # Integration API key
export UNIFI_SITE=default                # optional
export UNIFI_CLOUD_API_KEY=...           # optional, Site Manager key from unifi.ui.com
                                         # (UNIFI_SITE_MANAGER_API_KEY works too; not the same key as UNIFI_API_KEY)
# export UNIFI_VERIFY_TLS=1              # only if your console has a trusted certificate
```

Then, in Claude Code: *"check my unifi setup"*. Claude runs the info call and tells you whether
everything is wired up.

## Live checklist (first time)

```bash
U="python3 plugins/unifi/skills/unifi-ops/scripts/unifi.py"
$U info
$U sites list --table
$U devices list --table
$U clients list --table
$U wans list
$U report health --table
$U cloud hosts --table                       # if UNIFI_CLOUD_API_KEY is set
plugins/unifi/skills/unifi-ops/scripts/ssh_diag.sh root@192.168.1.1 system   # if SSH is set up
$U devices restart <an AP id> --dry-run      # prints the request, sends nothing (RESTART is the only device action the API accepts)
```

## Try it without a controller

```bash
export UNIFI_MOCK_DIR=$PWD/plugins/unifi/skills/unifi-ops/evals/fixtures
python3 plugins/unifi/skills/unifi-ops/scripts/unifi.py report health --table
```

The fixtures describe a synthetic home site. Writes are echoed back and never stored.

## What's inside

```
plugins/unifi/skills/unifi-ops/
  SKILL.md                 workflow and safety instructions Claude follows
  scripts/unifi.py         stdlib-only CLI over the Integration + Site Manager APIs
  scripts/ssh_diag.sh      read-only SSH diagnostics bundle for the console
  references/              API cheat sheets, SSH commands, troubleshooting playbooks
  evals/                   test prompts and mock fixtures
```

## Verified live

Read commands, `report health`, and the Site Manager commands were run against a UCG Fiber on
Network 10.6.106 (7 devices, 67 clients). `devices locate` was removed after that run: the
Integration API's device action enum is `RESTART` only. Port and client actions follow the
published API but have not been exercised on real hardware yet; the CLI's dry run shows the
request, and the controller's 400 lists the accepted values if one is rejected.

## Safety model

- Reads never prompt. Writes print the exact request and exit until `--yes` is given.
- Claude is instructed to show one plan per change, call out blast radius (dependent
  SSIDs/zones/policies, uplink ports, the management network, the gateway), and verify
  afterwards.
- Bulk voucher deletion without a filter and device un-adoption are refused or discouraged.
- SSH is read-only in the bundled script; state-changing shell commands are documented but not scripted.

## Not (yet) covered

UniFi Protect, Access and Talk; the legacy `/api/s/<site>` controller API; multiple consoles
in one session (use `UNIFI_HOST` per session, or Site Manager for the overview).

## License

MIT
