---
name: backup
description: "Slash command: list vzdump backups, run a backup, show backup jobs, restore a VM or container from an archive, or diagnose failed backup tasks on Proxmox VE 9. Pruning and restores over existing guests are planned and confirmed first."
argument-hint: "<list [vmid] | run <vmid> [storage=...] | jobs | restore <vmid> <archive volid> [key=value ...] | failures>"
disable-model-invocation: true
---

# /proxmox:backup

If the `proxmox:pve` skill is not loaded in this conversation, invoke it with the Skill tool and apply its safety contract.

Arguments: `$ARGUMENTS` = one of the forms in the argument hint. Use `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh` for calls and `${CLAUDE_PLUGIN_ROOT}/scripts/pve-task.sh <UPID>` to wait; only `OK` or `WARNINGS` count as success. Never print `PVE_TOKEN_SECRET`.

## list [vmid] (free)

1. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes` and, per node, `GET /nodes/<node>/storage content=backup` to find backup-capable storages (skip shared storages already visited).
2. Per storage: `GET /nodes/<node>/storage/<storage>/content content=backup [vmid=<vmid>]`. Print the volid, vmid, size, format and any notes or protected fields the response contains (other field names UNVERIFIED). An empty list from a storage whose `GET /nodes/<node>/storage` entry shows non-zero `used` is suspect (a PVEAuditor token got HTTP 200 and `[]` from a storage holding 431 backups on a PVE 9.2 cluster; cause UNVERIFIED): report "could not enumerate contents with this token", never "no backups", and suggest `pvesm list <storage>` over the SSH tier or a token with more privileges. Never conclude that no backups exist from an empty list alone.
3. Sort newest first per vmid.

## run <vmid> [key=value ...] (free unless pruning)

1. Resolve the node from `GET /cluster/resources type=vm`.
2. Storage: `storage=` from the arguments, else list backup-capable storages on that node and ask.
3. `POST /nodes/<node>/vzdump vmid=<vmid> storage=<storage> remove=0 [mode=snapshot|suspend|stop] [compress=zstd] [notes-template=...] [protected=1]`. Default `mode` is snapshot. Always pass `remove=0` unless the user asked for pruning. Returns a UPID; wait on it.
4. `remove` defaults to 1 on the API side and prunes by the storage retention, which is why the default call sets `remove=0`. If the user asks for `remove=1` explicitly, or passes `prune-backups=...`, treat the call as gated: show the PLAN block (which backups the retention would delete, from the `list` output), stop, and run only after the user says yes in a later message.

## jobs (free)

`${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /cluster/backup`, then `GET /cluster/backup/<id>/included_volumes` for a job the user asks about. Print id, schedule, enabled, storage, mode, vmid/all/pool, prune-backups, comment. Creating a job is free (`POST /cluster/backup` with `schedule=` and vzdump params); editing or deleting one is a change the user must ask for explicitly, and `DELETE /cluster/backup/<id>` is gated.

## restore <vmid> <archive volid> [key=value ...] (gated when the target exists)

1. Decide the guest type from the volid: `vzdump-qemu-*` restores a VM, `vzdump-lxc-*` a container.
2. Check `GET /cluster/resources type=vm` for the target `vmid`.
3. Target does not exist: restore is a free create. VM: `POST /nodes/<node>/qemu vmid=<vmid> archive=<volid> [storage= unique=1 start=1]`. CT: `POST /nodes/<node>/lxc vmid=<vmid> ostemplate=<volid> restore=1 [storage= unique=1 unprivileged=1]`.
4. Target exists: show the PLAN block with Current state (status, node, disks from `config`), Action (the same call plus `force=1`, which overwrites the existing guest), Effect (current disks and config are replaced by the archive; a running guest must be stopped first, which is a separate gated step), Revert (take a backup of the current guest first, offered as a free step), "Reply yes to proceed". Stop. Run only after the user says yes in a later message.
5. Wait on the UPID, read `status/current`, report.

## failures (free)

1. Per node from `GET /nodes`: `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/tasks typefilter=vzdump errors=1 limit=20`. Items carry `endtime, id, node, pid, pstart, starttime, status, type, upid, user`; `status` is the task's final status text, `OK` or the error message itself (confirmed on a PVE 9.2 cluster), so quote it before reading the log.
2. For each failed task: `GET /nodes/<node>/tasks/<url-encoded UPID>/log limit=500` and quote the lines that mention `ERROR`, the vmid and the storage.
3. Map the cause: no space on storage, snapshot not possible on that storage (suggest `mode=suspend` or `stop`), guest locked, storage not active, timeout. Compare with `GET /cluster/backup` to find the job and its options.
4. Propose the fix as a plan; do not change jobs, delete backups or re-run a backup without the user asking.

## Report

For every call: the exact call, the UPID, the `exitstatus:` line from pve-task.sh and the backup list or guest state after. If a task fails, show its log tail and do not retry a gated action on your own.
