# Homelab plugins for Claude Code

Three [Claude Code](https://claude.com/claude-code) plugins for running a homelab, published from one
marketplace named `jonasthim`. Each plugin installs on its own.

| Plugin | Operates | Needs |
|---|---|---|
| [truenas](truenas.md) | TrueNAS SCALE 25.x: pools, datasets, snapshots, shares, apps, alerts, updates, replication | API key, or SSH to the NAS |
| [proxmox](proxmox.md) | Proxmox VE 9: VMs, containers, snapshots, backups, storage, networking/SDN, HA, access control | API token (SSH optional) |
| [unifi](unifi.md) | UniFi networks: clients, devices, health, networks/VLANs, SSIDs, firewall | Integration API key (Site Manager key and SSH optional) |

```
claude plugin marketplace add jonasthim/claude
claude plugin install truenas@jonasthim    # and/or proxmox, unifi
```

## Pages

- [Getting started](getting-started.md): install, configure, first checks, update and uninstall
- [TrueNAS](truenas.md), [Proxmox VE](proxmox.md), [UniFi](unifi.md): setup and use per plugin
- [Safety model](safety.md): what Claude does on its own, what it asks first, and how secrets are handled
- [Building a mod](mods.md): add your own function-hook plugin to this marketplace, with a worked example

Each plugin's own README in [`plugins/`](https://github.com/jonasthim/claude/tree/main/plugins) is the full
reference; these pages summarise it.
