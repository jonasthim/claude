# claude

Claude Code plugins for running a homelab, published as one marketplace named `jonasthim`.

| Plugin | What it operates | Contents |
|---|---|---|
| [`truenas`](plugins/truenas) | TrueNAS SCALE 25.x: pools, datasets, snapshots, shares, apps, alerts, updates, replication | skill `truenas`, `tn.py` (JSON-RPC websocket or SSH `midclt`) |
| [`proxmox`](plugins/proxmox) | Proxmox VE 9: VMs, containers, snapshots, backups, storage, networking/SDN, HA, access control | skill `pve`, `/proxmox:*` commands, `proxmox-operator` agent, destructive-action guard hook |
| [`unifi`](plugins/unifi) | UniFi networks: client troubleshooting, health reports, guarded config changes, device actions | skill `unifi-ops`, `unifi.py` (Network Integration and Site Manager APIs), SSH diagnostics |

Each plugin installs on its own, so you only load what you run.

## Install

```
claude plugin marketplace add jonasthim/claude
claude plugin install truenas@jonasthim
claude plugin install proxmox@jonasthim
claude plugin install unifi@jonasthim
```

or from inside a session: `/plugin marketplace add jonasthim/claude`, then `/plugin install <plugin>@jonasthim`.

If the repository is private, the marketplace add needs GitHub access that can read it (`gh auth login`,
a credential helper or an SSH key). Otherwise clone it and add the local path:

```
gh repo clone jonasthim/claude ~/src/claude
claude plugin marketplace add ~/src/claude
```

Update later with `claude plugin marketplace update jonasthim` and `claude plugin update <plugin>@jonasthim`.
Each plugin's README covers its configuration (environment variables, API keys, SSH) and safety model.

## Develop

Load a plugin from a checkout for one session with `claude --plugin-dir plugins/<plugin>`. Tests per plugin:

```
(cd plugins/truenas && python3 -m unittest discover tests)               # truenas
bash plugins/proxmox/tests/run.sh                                          # proxmox
bash plugins/unifi/tests/smoke.sh                                          # unifi (offline, against fixtures)
```

Shared conventions are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Mods

The marketplace can also carry mods: Claude Code plugins of function hooks that add a pane, a band
above the prompt, a status line entry or a tool-call hook. None ship yet; see [mods/README.md](mods/README.md).

## License

MIT, see [LICENSE](LICENSE).
