---
name: vm
description: "Slash command: manage a QEMU virtual machine on Proxmox VE 9 (list, status, config, start, shutdown, stop, reboot, reset, suspend, resume, clone, migrate, destroy). Disruptive actions are planned and confirmed first."
argument-hint: "<list|status|config|start|shutdown|stop|reboot|reset|suspend|resume|clone|migrate|destroy> <vmid> [key=value ...]"
disable-model-invocation: true
---

# /proxmox:vm

Safety contract, tooling and workflow come from `proxmox:pve`; invoke it with the Skill tool if it is not loaded. Arguments: `$ARGUMENTS` = `<action> <vmid> [key=value ...]`. Every `key=value` after the VMID is passed to the API call unchanged.

## Resolve the guest

- `list`: `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /cluster/resources type=vm`, keep `type == "qemu"`, print vmid, name, node, status, template, hastate, lock. Done.
- Every other action: same call, find the item with the given `vmid`. Stop if it does not exist or if `type` is `lxc` (point to `/proxmox:ct`). Remember `node`.

## Free actions (run directly, then verify)

| Action | Call |
|---|---|
| status | `GET /nodes/<node>/qemu/<vmid>/status/current` |
| config | `GET /nodes/<node>/qemu/<vmid>/config` (add `current=1` for the live config; `GET .../pending` for staged changes) |
| start | `POST /nodes/<node>/qemu/<vmid>/status/start` (optional `timeout=`) returns a UPID |
| resume | `POST /nodes/<node>/qemu/<vmid>/status/resume` returns a UPID |
| clone | `newid` is required: if the user gave none, take `GET /cluster/nextid` and tell them it is not a reservation. `POST /nodes/<node>/qemu/<vmid>/clone newid=<n> [name= full=1 storage= format= target= snapname= pool=]`. Templates clone linked by default; `storage` and `format` need `full=1`. Returns a UPID |

## Gated actions (PLAN block first)

For each of these: read `status/current` (and `config` for destroy), then show the PLAN block from the safety contract (Target, Current state, Action with the exact call, Effect, Revert, "Reply yes to proceed"). Stop. Run the call only after the user says yes in a later message. The guard hook will ask once more when the command runs; that is expected.

| Action | Call | Notes |
|---|---|---|
| shutdown | `POST /nodes/<node>/qemu/<vmid>/status/shutdown [timeout= forceStop=1]` | ACPI shutdown; `forceStop=1` hard-stops after the timeout |
| stop | `POST /nodes/<node>/qemu/<vmid>/status/stop [timeout= overrule-shutdown=1]` | hard stop, like pulling the plug |
| reboot | `POST /nodes/<node>/qemu/<vmid>/status/reboot [timeout=]` | |
| reset | `POST /nodes/<node>/qemu/<vmid>/status/reset` | hard reset |
| suspend | `POST /nodes/<node>/qemu/<vmid>/status/suspend [todisk=1 statestorage=]` | |
| migrate | `GET /nodes/<node>/qemu/<vmid>/migrate` first (preconditions), then `POST /nodes/<node>/qemu/<vmid>/migrate target=<node> [online=1 with-local-disks=1 targetstorage= migration_type= bwlimit=]` | running VM needs `online=1`; local disks need `with-local-disks=1` |
| destroy | `DELETE /nodes/<node>/qemu/<vmid> [purge=1 destroy-unreferenced-disks=1]` | see below |

Destroy rules: the VM must be stopped (`status/current` shows `stopped`); if it is running, offer `shutdown` as a separate gated step first. Ask whether to pass `purge=1` (also removes the VM from backup jobs, replication and HA; required when the VM is HA-managed or replicated) and `destroy-unreferenced-disks=1` (the parameter exists and defaults to 0; its exact semantics are UNVERIFIED). State the disks that will be deleted from `config`. There is no revert; name the newest backup from `GET /nodes/<node>/storage/<storage>/content content=backup vmid=<vmid>` when one exists, otherwise say that none exists.

## Verify and report

Pipe each UPID into `${CLAUDE_PLUGIN_ROOT}/scripts/pve-task.sh -`, re-read `status/current`, then report the call, the UPID, the `exitstatus:` line and the state after (workflow steps 5 and 6 of the pve skill).
