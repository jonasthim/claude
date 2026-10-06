---
name: pve
description: "Operate and troubleshoot Proxmox VE 9 clusters through the REST API (API token) or SSH to nodes. Covers inventory and health, VM (qm) and LXC container (pct) lifecycle, snapshots, vzdump backups and restores, cloud-init templates and provisioning, storage, networking and SDN, cluster, HA and node maintenance, users, roles and API tokens. Use this whenever the user mentions Proxmox, PVE, pvesh, qm, pct, vzdump, a hypervisor node, a homelab cluster, or a VMID, even when they do not say Proxmox explicitly. Every destructive or disruptive action is planned and confirmed before it runs."
argument-hint: "[task or question about your Proxmox cluster]"
---

# Proxmox VE 9 operations

Facts in this skill and its references are condensed from Proxmox 9.x sources; items
marked UNVERIFIED were not confirmed. When in doubt, ask the API: `pvesh usage <path> -v`
over SSH, or the node-served docs at `https://NODE:8006/pve-docs/`.

## 1. Safety contract (read first)

Two classes of action. Gated actions are planned and confirmed; free actions run directly.

Gated set: destroy; stop/reset/shutdown/reboot/suspend of a guest; snapshot rollback/delete; migrate (guest or HA); template conversion; disk/volume move/unlink or `delete=` on config; backup/volume deletion (`pvesm free|remove|prune-backups`, `pveam remove`, `prunebackups`, `vzdump --remove 1`/`--prune-backups`, `rm` of `vzdump-*`); any `DELETE`/`pvesh delete`; node reboot/shutdown/stopall/migrateall/suspendall; network apply (`PUT /nodes/{node}/network`, `ifreload`/`ifdown`/`ifup` over SSH); SDN apply/rollback (`PUT /cluster/sdn`, `/cluster/sdn/rollback`); HA `remove|set|migrate|relocate|crm-command`, `disarm-ha`, HA resource `state=stopped|disabled`; `pvecm delnode|expected|add|create|qdevice`; bulk shutdown/suspend/migrate; `apt upgrade/dist-upgrade/full-upgrade/remove/purge/autoremove` over SSH; `systemctl stop|restart|reboot|poweroff|halt|isolate`, `reboot|shutdown|poweroff|halt|init 0|init 6` over SSH.

Free set: all GETs; start/resume; create VM/CT; clone; set config without `delete`; snapshot create; vzdump run without explicit prune; create backup job; HA resource add; SDN/network object create/edit (staged, not applied); storage add/edit; user/role/token create; apt update (refresh); task log reads.

For every gated action, print exactly this block, then stop and wait:

```
PLAN
Target:        <guest/node/object, e.g. VM 101 "web01" on pve1>
Current state: <from the API: status, lock, HA state, snapshots, disks>
Action:        <exact call(s), e.g. pve-api.sh DELETE /nodes/pve1/qemu/101 purge=1>
Effect:        <what changes, what is lost, expected downtime>
Revert:        <how to undo, or "irreversible">
Reply yes to proceed.
```

Rules (they exist because the API has no undo and the guard hook is the only backstop):

- Execute a gated action only after an explicit "yes" (or equivalent) in a later user
  message. A "yes" given before the PLAN was shown does not count. One confirmation covers
  one PLAN block; a new target needs a new PLAN.
- Running as a subagent (no way to prompt): return the PLAN block as the result and stop,
  unless the task message contains `CONFIRMED: <action> <target>` for that exact action.
- Never print `PVE_TOKEN_SECRET` or any token value; refer to it as "set (hidden)".
- Never set `PVE_INSECURE=1` yourself; only the user opts in to skipping TLS verification.
- Never hardcode VMIDs, node names or storage ids; discover them via the API every time.
- Never work around the guard hook's permission prompt: no `eval`, no base64 wrapping, no
  copying `pve-api.sh` to another name, no `pvesh`/`curl` substitutes to dodge a rule.
- Do not auto-retry a failed gated task; report the task log and ask.

## 2. Setup

| Variable | Default | Meaning |
|---|---|---|
| `PVE_HOST` | (required) | `host[:port]` or full `https://host:8006`; port 8006 added when absent |
| `PVE_TOKEN_ID` | (required) | `USER@REALM!TOKENID` |
| `PVE_TOKEN_SECRET` | (required) | token value; never printed |
| `PVE_CA_CERT` | unset | CA bundle path passed to `curl --cacert` |
| `PVE_INSECURE` | unset | `1` skips TLS verification (`curl -k`), warns once; user opt-in only |
| `PVE_TIMEOUT` | `30` | curl `--max-time` seconds |
| `PVE_API_RAW` | unset | `1` prints the full `{"data":...}` envelope |
| `PVE_API_DEBUG` | unset | `1` prints method and URL to stderr (no header) |
| `PVE_SSH_HOST` | host part of `PVE_HOST` | node for the SSH tier |
| `PVE_SSH_USER` | `root` | SSH user (`pvesh`, `qm`, `pct` need root on the node) |
| `PVE_SSH_PORT` | `22` | SSH port |
| `PVE_SSH_KEY` | unset | identity file (`ssh -i`) |
| `PVE_SSH_OPTS` | unset | extra ssh options |

First call in a session, and again after any 401 or 403:

```
${CLAUDE_PLUGIN_ROOT}/scripts/pve-doctor.sh
```

Exit codes: 0 ok; 1 missing prerequisite or env var; 2 transport/TLS; 3 HTTP 401 (bad or
expired token); 4 HTTP 403 (token lacks privileges, see `references/permissions.md`);
5 other API error. Its capability summary (read-only / operator / admin-capable) tells you
which gated actions will fail with 403 before you plan them.

## 3. Tooling

All scripts: bash + curl + jq only, `-h` for usage, secret never printed.

`${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh <GET|POST|PUT|DELETE> <path> [key=value ...]`

- Path with or without leading `/` or `/api2/json`; may carry `?query`.
- GET/DELETE send params as URL query (never a body); POST/PUT send a form body with
  `--data-urlencode`, so values with `,` `=` `:` `/` are safe: `net0=virtio,bridge=vmbr0`.
- Prints `.data` (bare UPID string, or pretty JSON). Errors on stderr as
  `pve-api: HTTP <code> <METHOD> <path>: <message>` plus `  <param>: <msg>` lines.
- Exit: 0 2xx; 1 usage/env/missing curl or jq; 2 transport/TLS; 3 HTTP 4xx; 4 HTTP 5xx.

```
${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /cluster/resources type=vm
${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh POST /nodes/pve1/qemu/100/status/start
${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh PUT /nodes/pve1/qemu/100/config memory=4096 digest=DIGEST
```

`${CLAUDE_PLUGIN_ROOT}/scripts/pve-task.sh <UPID|-> [--timeout SECS] [--interval SECS] [--no-log]`

- Polls `GET /nodes/{node}/tasks/{upid}/status` until stopped (default timeout 600 s),
  then prints the task log and a final `exitstatus: <value>` line.
- Exit: 0 `OK` or `WARNINGS: n`; 1 task failed; 2 API/transport error; 3 bad UPID; 4 timeout.
- Pipe form: `pve-api.sh POST .../status/shutdown timeout=60 | pve-task.sh -`

`${CLAUDE_PLUGIN_ROOT}/scripts/pve-ssh.sh [-n HOST] [--check] <command> [args...]`

- Runs the command on a node with `ssh -o BatchMode=yes`; exit code is the remote one
  (255 = ssh failure, 1 = no host). `--check` runs `pveversion`.
- Use SSH only for what the API cannot do: node maintenance mode
  (`ha-manager crm-command node-maintenance enable|disable <node>`), package upgrades
  (`apt dist-upgrade`; there is no API endpoint), `pvecm add|delnode`, hand edits of
  `/etc/network/interfaces` followed by `ifreload -a`, `pct enter|exec|push|pull`,
  `pvereport`, `pve8to9`, `qm showcmd <vmid> --pretty`.

Raw curl, if a script is unavailable (same header the scripts use):

```
curl -sS --cacert "$PVE_CA_CERT" -H "Authorization: PVEAPIToken=${PVE_TOKEN_ID}=${PVE_TOKEN_SECRET}" \
  --data-urlencode 'net0=virtio,bridge=vmbr0' "https://HOST:8006/api2/json/nodes/pve1/qemu/100/config" -X PUT
```

## 4. Workflow

1. Discover. `GET /cluster/resources type=vm` gives every guest with its `node`, `type`
   (`qemu`|`lxc`), `status`, `hastate`, `lock`, `template`; use it to map vmid to node, never
   guess. Then `GET /nodes/{node}/{qemu|lxc}/{vmid}/status/current` for live state and
   `GET .../config` for the `digest` and current keys.
2. Plan. Classify the action (section 1). Gated: print the PLAN block and stop.
3. Confirm. Proceed only on an explicit yes in a later message.
4. Execute. Capture the UPID the call returns (create, destroy, start, stop, shutdown,
   reboot, reset, suspend, resume, clone, migrate, resize, snapshot create/rollback/delete,
   template, `POST .../config`, vzdump, `PUT /nodes/{node}/network`, `POST .../apt/update`).
5. Verify. `pve-task.sh <UPID>`; only `OK` or `WARNINGS: n` count as success. Re-read
   `status/current` or `config` to confirm the end state.
6. Report. Calls made, UPIDs with exit status, state after, revert path.

Details that bite: `GET /cluster/nextid` is not a reservation, handle "VM N already
exists"; pass `digest` from `GET .../config` on every config write and resize to avoid
lost updates; `PUT .../config` is synchronous (returns null), `POST .../config` is async
(UPID) and is the one to use for long disk operations.

## 5. Quick reference

Paths are relative to `/api2/json`; `N` is a node, `V` a vmid, `S` a storage id.

| Task | Call |
|---|---|
| Cluster/quorum | `GET /cluster/status`; `GET /nodes`; `GET /nodes/N/status` |
| Inventory | `GET /cluster/resources type=vm` (or `type=storage`, `type=node`, `type=sdn`) |
| VM list/state | `GET /nodes/N/qemu`; `GET /nodes/N/qemu/V/status/current`; `GET .../config`; `GET .../pending` |
| VM start | `POST /nodes/N/qemu/V/status/start` (timeout, machine, targetstorage) |
| VM shutdown (gated) | `POST .../status/shutdown timeout=60 forceStop=1` (keepActive root-only) |
| VM stop (gated) | `POST .../status/stop timeout=30 overrule-shutdown=1` |
| VM reboot/reset/suspend/resume | `POST .../status/reboot timeout=60`; `.../reset`; `.../suspend todisk=1 statestorage=S`; `.../resume` (reset/suspend/reboot gated) |
| CT lifecycle | `POST /nodes/N/lxc/V/status/{start,stop,shutdown,reboot,suspend,resume}`; CT has no reset |
| VM config edit | `PUT /nodes/N/qemu/V/config memory=4096 digest=D`; removal `delete=scsi1` is gated |
| CT config edit | `PUT /nodes/N/lxc/V/config cores=2 digest=D` |
| Snapshots | `GET /nodes/N/qemu/V/snapshot`; `POST .../snapshot snapname=X vmstate=1 description=...`; `POST .../snapshot/X/rollback start=1` (gated); `DELETE .../snapshot/X` (gated); same paths under `/lxc` |
| Clone | `POST /nodes/N/qemu/V/clone newid=M name=X full=1 storage=S target=N2`; templates clone linked unless `full=1`; CT: `POST /nodes/N/lxc/V/clone newid=M hostname=X full=1` |
| Migrate (gated) | `GET /nodes/N/qemu/V/migrate` (preconditions); `POST .../migrate target=N2 online=1 with-local-disks=1`; CT: `POST /nodes/N/lxc/V/migrate target=N2 restart=1` (running CTs need restart) |
| Template (gated) | `POST /nodes/N/qemu/V/template` |
| Destroy (gated) | `DELETE /nodes/N/qemu/V purge=1 destroy-unreferenced-disks=1`; CT accepts `force=1` (even if running); stop the guest first; HA/replicated guests need `purge=1` |
| Resize | `PUT /nodes/N/qemu/V/resize disk=scsi0 size=+10G digest=D` (also `/lxc`) |
| Create VM | `GET /cluster/nextid`; `POST /nodes/N/qemu vmid=V name=X memory=2048 cores=2 net0=virtio,bridge=vmbr0 scsihw=virtio-scsi-pci scsi0=S:32` (`S:32` as a new 32 GiB disk is UNVERIFIED; `S:0,import-from=VOLID` is documented) |
| Create CT | `POST /nodes/N/lxc vmid=V ostemplate=local:vztmpl/FILE hostname=X rootfs=S:8 cores=2 memory=2048 net0=name=eth0,bridge=vmbr0,ip=dhcp unprivileged=1 ssh-public-keys=... start=1` |
| Backup run | `POST /nodes/N/vzdump vmid=V storage=S mode=snapshot compress=zstd` (`remove=1`/`prune-backups=` gated) |
| Backup list | `GET /nodes/N/storage/S/content content=backup vmid=V` |
| Backup jobs | `GET /cluster/backup`; `GET /cluster/backup/ID`; `GET /cluster/backup/ID/included_volumes` |
| Restore VM | `POST /nodes/N/qemu vmid=V archive=S:backup/FILE storage=S2 force=1` (gated when V exists) |
| Restore CT | `POST /nodes/N/lxc vmid=V ostemplate=S:backup/FILE restore=1 storage=S2 force=1` |
| Tasks | `GET /nodes/N/tasks typefilter=vzdump errors=1 limit=20`; `GET /cluster/tasks`; `GET /nodes/N/tasks/UPID/status`; `.../log start=0 limit=500` |
| Storage | `GET /storage`; `GET /nodes/N/storage`; `GET /nodes/N/storage/S/status`; `GET .../content content=vztmpl` |
| Templates/ISOs | `POST /nodes/N/storage/S/download-url url=... content=vztmpl filename=...` |
| HA | `GET /cluster/ha/resources`; `POST /cluster/ha/resources sid=ct:105 state=started`; `GET /cluster/ha/rules`; `GET /cluster/ha/status/current` |
| Node ops (gated) | `POST /nodes/N/status command=reboot`; `POST /nodes/N/stopall`; `POST /nodes/N/migrateall` |
| Updates | `POST /nodes/N/apt/update` (refresh, free); `GET /nodes/N/apt/update` (pending); `GET /nodes/N/apt/versions`; upgrade only via SSH (gated) |
| Access | `GET /access/permissions`; `GET /access/users`; `POST /access/users/USER/token/NAME privsep=1`; `PUT /access/acl path=/vms roles=PVEVMAdmin tokens=USER!NAME propagate=1` |

## 6. Domain guides

Read the guide before working in its area; each is self-contained.

| File | Read when |
|---|---|
| `references/api-cheatsheet.md` | you need an endpoint, its params, privilege, or whether it returns a UPID |
| `references/cli-cheatsheet.md` | working over SSH with pvesh, qm, pct, vzdump, pvesm, pveam, pvecm, ha-manager, pvenode, pveum |
| `references/permissions.md` | 403 errors, creating tokens, choosing roles and ACL paths |
| `references/cloud-init.md` | building cloud-init templates, cloning them, sshkeys/ipconfig encoding |
| `references/storage.md` | storage types, content listing, uploads, download-url, pruning, resize/move |
| `references/networking-sdn.md` | bridges, bonds, VLANs, staged network apply, SDN zones/vnets/subnets, firewall |
| `references/cluster-ha.md` | cluster membership, node reboot/maintenance, bulk actions, HA resources and rules |
| `references/backups.md` | vzdump modes and options, backup jobs, restores, failed backups |
| `references/troubleshooting.md` | HTTP error codes, task logs, locks, digest conflicts, daemons, script exit codes |
| `references/pve9-changes.md` | anything that worked on PVE 8 and fails on 9 |

## 7. Pitfalls

- POST/PUT bodies are form-encoded (`application/x-www-form-urlencoded`) or JSON with a
  `Content-Type`; query params override body params; GET and DELETE must not send a body
  (HTTP 501). The scripts already do this right.
- VM `sshkeys` is strictly percent-encoded (`%20` space, `%2B` plus, `%2F` slash, `%3D`
  equals, `%40` at, `%0A` newline; `+` is not a space); CT `ssh-public-keys` is plain text.
- Always use `/api2/json`, never `/api2/extjs` (it returns 200 on every error).
- `skiplock`, `keepActive`, `migratedfrom` are root@pam only; API tokens cannot use them.
- `import-from=<absolute path>` is root@pam only; tokens must import from a volid
  (content type `images` or `import`), so download the image into storage first.
- Destroy fails for HA-managed or replicated guests unless `purge=1`.
- Tokens expire (`expire`); a 401 with "access expired" means a new token, not a bug.
- `VM.Monitor` was removed in PVE 9; `/monitor` needs `Sys.Audit`/`Sys.Modify`.
- HA groups are deprecated in PVE 9 and auto-migrated to node-affinity rules; create rules
  under `/cluster/ha/rules`, not groups.
- `GET .../config` shows pending values by default; add `current=1` for the live config.
- A 500 with "VM already running" or "VM N already exists" is a state conflict, not a bug;
  re-discover and re-plan.
