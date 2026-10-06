# claude

Claude Code plugins for running a homelab, published as one marketplace named `jonasthim`. Talk to Claude
about your NAS, hypervisor or network and it inspects, troubleshoots and changes them through their official
APIs. It asks before anything destructive.

| Plugin | What it operates | You need | Contents |
|---|---|---|---|
| [`truenas`](plugins/truenas) | TrueNAS SCALE 25.x: pools, datasets, snapshots, shares, apps, alerts, updates, replication | API key; SSH optional | skill `truenas`, `tn.py` |
| [`proxmox`](plugins/proxmox) | Proxmox VE 9: VMs, containers, snapshots, backups, storage, networking/SDN, HA, access control | API token; SSH optional | skill `pve`, `/proxmox:*` commands, `proxmox-operator` agent, guard hook |
| [`unifi`](plugins/unifi) | UniFi networks: client troubleshooting, health reports, networks/VLANs, SSIDs, firewall, device actions | Integration API key; Site Manager key and SSH optional | skill `unifi-ops`, `unifi.py`, SSH diagnostics |

Each plugin installs on its own, so you only load what you run. **Full documentation: [docs/](docs/index.md).**

## Quick start

**1. Install**

```
claude plugin marketplace add jonasthim/claude
claude plugin install truenas@jonasthim
claude plugin install proxmox@jonasthim
claude plugin install unifi@jonasthim
```

Or inside a session: `/plugin marketplace add jonasthim/claude`, then `/plugin install <plugin>@jonasthim`. If
the repository is private and the add fails, clone it (`gh repo clone jonasthim/claude ~/src/claude`) and
run `claude plugin marketplace add ~/src/claude`.

**2. Configure**: export credentials in the shell that starts Claude Code, ideally from a file only you can read:

```
# ~/.config/homelab.env  (chmod 600), then: source ~/.config/homelab.env && claude
export TRUENAS_HOST=nas.example.lan     TRUENAS_API_KEY='1-...'
export PVE_HOST=pve1.example.lan        PVE_TOKEN_ID='claude@pve!ro'  PVE_TOKEN_SECRET='...'
export PVE_CA_CERT=~/.config/pve-root-ca.pem
export UNIFI_HOST=192.168.1.1           UNIFI_API_KEY='...'
```

| Plugin | Where to create the credential | Read-only to start with |
|---|---|---|
| truenas | Web UI → your user icon → *API Keys* → *Add* | Key of a `READONLY_ADMIN` user |
| proxmox | On a node: `pveum user token add ...`, or *Datacenter → Permissions → API Tokens*. You create it, not Claude: the secret is shown once | `PVEAuditor` on `/` |
| unifi | Network app → *Settings → Control Plane → Integrations → API Keys* | Key of a read-only admin |

Every variable is listed on the plugin pages: [TrueNAS](docs/truenas.md), [Proxmox VE](docs/proxmox.md),
[UniFi](docs/unifi.md).

**3. Check the setup**: start a new session and ask "is my NAS healthy?", run `/proxmox:doctor`, or ask
"check my unifi setup".

## What to ask

| TrueNAS | Proxmox VE | UniFi |
|---|---|---|
| "Is my NAS healthy? Anything I should worry about?" | `/proxmox:status` for a cluster overview | "Why does my laptop keep dropping off wifi?" |
| "Snapshot tank/photos, then make the photos SMB share read-only for guests." | "Snapshot 101 before I upgrade it." | "Give me a health report." |
| "Jellyfin is stuck deploying, figure out why and fix it." | "Why did last night's backup of 105 fail?" | "Create a guest network on VLAN 30 with its own SSID." |
| "Which snapshots on tank/backup are older than 60 days?" | `/proxmox:vm`, `/proxmox:ct`, `/proxmox:snapshot`, `/proxmox:backup` | "Restart the hallway AP." |

## Safety model

- **Reads run freely.**
- **Destructive or disruptive actions need your yes.** Claude shows a plan first and waits for your answer.
  A code-level gate backs this up: `tn.py --confirm`, `unifi.py --yes` (with `--dry-run` to preview), and
  for Proxmox a guard hook that prompts even in auto mode.
- **Secrets stay out of the chat.** Claude never asks for or prints credentials, redacts them in output, and
  never turns TLS verification off on its own.
- **Least privilege** is your last line: a read-only key or token cannot do damage.

The details and a per-plugin comparison are in [docs/safety.md](docs/safety.md).

## Update and uninstall

```
claude plugin marketplace update jonasthim
claude plugin update <plugin>@jonasthim
claude plugin uninstall <plugin>@jonasthim
```

## Documentation

| Page | |
|---|---|
| [Getting started](docs/getting-started.md) | Install, configure, first checks, moving from the old repositories |
| [TrueNAS](docs/truenas.md), [Proxmox VE](docs/proxmox.md), [UniFi](docs/unifi.md) | Credentials, variables, commands, safety and troubleshooting per plugin |
| [Safety model](docs/safety.md) | What runs on its own, what asks first, secrets and TLS |
| [Building a mod](docs/mods.md) | Function-hook plugins, with a worked, tested example |

Each plugin's README under [`plugins/`](plugins) is its full reference.

## Develop

Load a plugin from a checkout for one session with `claude --plugin-dir plugins/<plugin>`. Tests per plugin:

```
(cd plugins/truenas && python3 -m unittest discover tests)               # truenas
bash plugins/proxmox/tests/run.sh                                          # proxmox
bash plugins/unifi/tests/smoke.sh                                          # unifi (offline, against fixtures)
```

UniFi also runs without a controller: `export UNIFI_MOCK_DIR=$PWD/plugins/unifi/skills/unifi-ops/evals/fixtures`.
Shared conventions are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Mods

The marketplace can also carry mods: plugins made of function hooks that intercept tool calls, decide
permissions, or add a status line entry, a band or a pane. None ship yet. [docs/mods.md](docs/mods.md) walks
through building one.

## License

MIT, see [LICENSE](LICENSE).
