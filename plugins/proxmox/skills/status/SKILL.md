---
name: status
description: "Slash command: read-only overview of the Proxmox VE cluster (nodes, quorum, guests per node, HA, storage usage), or detail for one node, VMID or storage. Never changes anything."
argument-hint: "[node | vmid | storage]"
disable-model-invocation: true
---

# /proxmox:status

If the `proxmox:pve` skill is not loaded in this conversation, invoke it with the Skill tool and apply its safety contract. This command is read-only: every call below is a GET and runs freely without confirmation.

Argument: `$ARGUMENTS` (empty, a node name, a numeric VMID, or a storage id). Use `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh` for every call. Never print `PVE_TOKEN_SECRET`. If a call fails with HTTP 401 or 403, stop and tell the user to run `/proxmox:doctor`.

## No argument: cluster overview

1. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /cluster/status` for the node list and quorum. The response is a mixed array (confirmed on a PVE 9.2 cluster); branch on `type`: the single `type=cluster` item carries `name`, `nodes`, `quorate` and `version`; each `type=node` item carries `name`, `nodeid`, `ip`, `online`, `local` and `level`. Report quorum from the cluster item and online/offline from the node items; do not invent other fields.
2. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /cluster/resources` and group items by `type`:
   - `node`: `status`, `cpu`/`maxcpu`, `mem`/`maxmem`, `uptime`.
   - `qemu` and `lxc`: count running and stopped per `node`; list `template` guests separately; note any `lock` or `hastate`.
   - `storage`: compute `disk / maxdisk`; flag every storage at or above 80 % with a WARNING line.
3. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /cluster/ha/resources` and `GET /cluster/ha/status/current`: list HA resources with their `state`; say "no HA resources" when the list is empty.
4. Report as a short table: per node (status, guests running/stopped, cpu %, mem %), then storages with usage, then HA summary, then warnings.

## Node argument

1. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/status` for load, memory, uptime and version fields present in the response.
2. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/qemu` and `GET /nodes/<node>/lxc` for guests on that node.
3. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/storage` for storages visible on that node.
4. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/tasks errors=1 limit=10` for recent failed tasks; print type, id, user, starttime, status and the UPID of each (items carry `endtime, id, node, pid, pstart, starttime, status, type, upid, user`; confirmed on a PVE 9.2 cluster). `status` is the task's final status text: `OK` or the error message itself, so quote it as the failure reason.

## VMID argument

1. Resolve the node and guest type with `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /cluster/resources type=vm` (match `vmid`; use `type` qemu or lxc). Stop with a clear message if the VMID does not exist.
2. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/<qemu|lxc>/<vmid>/status/current`.
3. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/<qemu|lxc>/<vmid>/config` (shows pending values by default; add `current=1` for the live config).
4. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/<qemu|lxc>/<vmid>/snapshot` for the snapshot list.
5. Report: status, uptime, cores/memory, disks, network interfaces, HA state, lock, pending changes, snapshots.

## Storage argument

1. Find the storage items for that id in `GET /cluster/resources type=storage` (one per node).
2. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/storage/<storage>/status` on one node where it is active.
3. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/storage/<storage>/content` and summarise counts per `content` type (images, backup, iso, vztmpl, snippets, import). An empty list from a storage whose `status` or `/cluster/resources` item shows non-zero `used`/`disk` is suspect (a PVEAuditor token got `[]` from a storage holding 431 backups on a PVE 9.2 cluster; cause UNVERIFIED): report "could not enumerate contents with this token", not "no content" or "no backups", and suggest `pvesm list <storage>` over the SSH tier or a token with more privileges.
4. Report usage, enabled/active state, content types and the warning when usage is at or above 80 %.

Do not run any POST, PUT or DELETE from this command. Suggest `/proxmox:vm`, `/proxmox:ct`, `/proxmox:snapshot` or `/proxmox:backup` when the user wants to act on what they see.
