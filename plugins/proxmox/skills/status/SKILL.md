---
name: status
description: "Slash command: read-only overview of the Proxmox VE cluster (nodes, quorum, guests per node, HA, storage usage), or detail for one node, VMID or storage. Never changes anything."
argument-hint: "[node | vmid | storage]"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET *)
---

# /proxmox:status

Safety contract and tooling come from `proxmox:pve`; invoke it with the Skill tool if it is not loaded. Read-only: every call is a GET and runs without confirmation; run each as a single plain command (no pipes) so the pre-approved rule applies. Argument: `$ARGUMENTS` (empty, a node name, a numeric VMID, or a storage id). On HTTP 401 or 403 stop and point the user to `/proxmox:doctor`.

## No argument: cluster overview

1. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /cluster/status` for quorum and node list: report quorum from the `type=cluster` item and online/offline from the `type=node` items (shape in the pve quick reference); do not invent other fields.
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
4. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/tasks errors=1 limit=10` for recent failed tasks; print type, id, user, starttime, status and the UPID of each (fields in the pve quick reference; `status` is `OK` or the error text itself, quote it as the failure reason).

## VMID argument

1. Resolve the node and guest type with `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /cluster/resources type=vm` (match `vmid`; use `type` qemu or lxc). Stop with a clear message if the VMID does not exist.
2. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/<qemu|lxc>/<vmid>/status/current`.
3. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/<qemu|lxc>/<vmid>/config` (shows pending values by default; add `current=1` for the live config).
4. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/<qemu|lxc>/<vmid>/snapshot` for the snapshot list.
5. Report: status, uptime, cores/memory, disks, network interfaces, HA state, lock, pending changes, snapshots.

## Storage argument

1. Find the storage items for that id in `GET /cluster/resources type=storage` (one per node).
2. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/storage/<storage>/status` on one node where it is active.
3. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/storage/<storage>/content` and summarise counts per `content` type (images, backup, iso, vztmpl, snippets, import). An empty list from a storage whose `status` or `/cluster/resources` item shows non-zero `used`/`disk` is suspect: apply the empty-content-list pitfall from the pve skill (report "could not enumerate contents with this token", never "no content").
4. Report usage, enabled/active state, content types and the warning when usage is at or above 80 %.

Do not run any POST, PUT or DELETE from this command. Suggest `/proxmox:vm`, `/proxmox:ct`, `/proxmox:snapshot` or `/proxmox:backup` when the user wants to act on what they see.
