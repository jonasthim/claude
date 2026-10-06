# claude

Claude Code plugins for running a homelab, published as one marketplace named `jonasthim`. Talk to Claude
about your NAS, hypervisor, network, reverse proxy or identity provider and it inspects, troubleshoots and
changes them through their official APIs. It asks before anything destructive.

| Plugin | What it operates | You need | Contents |
|---|---|---|---|
| [`truenas`](plugins/truenas) | TrueNAS SCALE 25.x: pools, datasets, snapshots, shares, apps, alerts, updates, replication | API key, or SSH to the NAS | skill `truenas`, `tn.py` |
| [`proxmox`](plugins/proxmox) | Proxmox VE 9: VMs, containers, snapshots, backups, storage, networking/SDN, HA, access control | API token; SSH optional | skill `pve`, `/proxmox:*` commands, `proxmox-operator` agent, guard hook |
| [`unifi`](plugins/unifi) | UniFi networks: client troubleshooting, health reports, networks/VLANs, SSIDs, firewall, device actions | Integration API key; Site Manager key and SSH optional | skill `unifi-ops`, `unifi.py`, SSH diagnostics |
| [`pangolin`](plugins/pangolin) | Pangolin reverse proxy: published resources, sites and tunnels, targets and health checks, rules, access settings | Integration API key | skill `pangolin`, `pangolin.py`, `/pangolin:doctor` |
| [`authentik`](plugins/authentik) | authentik identity provider: refused logins, applications, providers, groups, bindings, outposts, events | API token | skill `authentik`, `authentik.py`, `/authentik:doctor` |

Each plugin installs on its own, so you only load what you run. **Full documentation: [docs/](docs/index.md).**

## Quick start

**1. Install**

```
claude plugin marketplace add jonasthim/claude
claude plugin install truenas@jonasthim
claude plugin install proxmox@jonasthim
claude plugin install unifi@jonasthim
claude plugin install pangolin@jonasthim
claude plugin install authentik@jonasthim
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
export PANGOLIN_HOST=https://api.example.com  PANGOLIN_API_KEY='...'  PANGOLIN_ORG=my-org
export AUTHENTIK_HOST=https://auth.example.com  AUTHENTIK_TOKEN='...'
```

| Plugin | Where to create the credential | Read-only to start with |
|---|---|---|
| truenas | Web UI → your user icon → *API Keys* → *Add* | Key of a `READONLY_ADMIN` user |
| proxmox | On a node: `pveum user token add ...`, or *Datacenter → Permissions → API Tokens*. You create it, not Claude: the secret is shown once | `PVEAuditor` on `/` |
| unifi | Network app → *Settings → Control Plane → Integrations → API Keys* | Key of a read-only admin |
| pangolin | Dashboard → *Organization → API Keys* (the Integration API must be enabled on the server) | Key with only list and get permissions |
| authentik | *Directory → Tokens and App passwords*: an API token, expiring off, for a service account | Service account in the group `authentik Read-only` |

Every variable is listed on the plugin pages: [TrueNAS](docs/truenas.md), [Proxmox VE](docs/proxmox.md),
[UniFi](docs/unifi.md), [Pangolin](docs/pangolin.md), [authentik](docs/authentik.md).

**3. Check the setup**: start a new session and ask "is my NAS healthy?", run `/proxmox:doctor`,
`/pangolin:doctor` or `/authentik:doctor`, or ask "check my unifi setup".

## What to ask

| TrueNAS | Proxmox VE | UniFi |
|---|---|---|
| "Is my NAS healthy? Anything I should worry about?" | `/proxmox:status` for a cluster overview | "Why does my laptop keep dropping off wifi?" |
| "Snapshot tank/photos, then make the photos SMB share read-only for guests." | "Snapshot 101 before I upgrade it." | "Give me a health report." |
| "Jellyfin is stuck deploying, figure out why and fix it." | "Why did last night's backup of 105 fail?" | "Create a guest network on VLAN 30 with its own SSID." |
| "Which snapshots on tank/backup are older than 60 days?" | `/proxmox:vm`, `/proxmox:ct`, `/proxmox:snapshot`, `/proxmox:backup` | "Restart the hallway AP." |

| Pangolin | authentik |
|---|---|
| "Why is app.example.com a bad gateway?" | "Bob gets access denied on Dashboards, why?" |
| "Which of my published apps have no login in front of them?" | "Give me a health report of my SSO." |
| "Publish the service on 198.51.100.5:8080 as notes.example.com behind SSO." | "Add an OIDC application for the wiki; only the staff group may use it." |
| "Add a health check to the wiki's second target." | "Who is a superuser, and which tokens never expire?" |

## Safety model

- **Reads run freely.**
- **Destructive or disruptive actions need your yes.** Claude shows a plan first and waits for your answer.
  A code-level gate backs this up: `tn.py --confirm`, `--yes` on `unifi.py`, `pangolin.py` and
  `authentik.py` (with `--dry-run` to preview), and for Proxmox a guard hook that prompts even in auto mode.
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
| [TrueNAS](docs/truenas.md), [Proxmox VE](docs/proxmox.md), [UniFi](docs/unifi.md), [Pangolin](docs/pangolin.md), [authentik](docs/authentik.md) | Credentials, variables, commands, safety and troubleshooting per plugin |
| [Safety model](docs/safety.md) | What runs on its own, what asks first, secrets and TLS |
| [Building a mod](docs/mods.md) | Function-hook plugins, with a worked, tested example |

Each plugin's README under [`plugins/`](plugins) is its full reference.

## Develop

Load a plugin from a checkout for one session with `claude --plugin-dir plugins/<plugin>`. Tests per plugin:

```
(cd plugins/truenas && python3 -m unittest discover tests)               # truenas
bash plugins/proxmox/tests/run.sh                                          # proxmox
bash plugins/unifi/tests/smoke.sh                                          # unifi (offline, against fixtures)
(cd plugins/pangolin && python3 -m unittest discover tests)              # pangolin (offline, fixtures and a mock server)
(cd plugins/authentik && python3 -m unittest discover tests)             # authentik (offline, fixtures and a mock server)
```

UniFi, Pangolin and authentik also run without the real system: point `UNIFI_MOCK_DIR`, `PANGOLIN_MOCK_DIR`
or `AUTHENTIK_MOCK_DIR` at the plugin's `evals/fixtures` folder.
Shared conventions are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Mods

The marketplace can also carry mods: plugins made of function hooks that intercept tool calls, decide
permissions, or add a status line entry, a band or a pane. None ship yet. [docs/mods.md](docs/mods.md) walks
through building one.

## License

MIT, see [LICENSE](LICENSE).
