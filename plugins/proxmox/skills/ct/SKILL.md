---
name: ct
description: "Slash command: manage an LXC container on Proxmox VE 9 (list, status, config, create, start, shutdown, stop, reboot, migrate, destroy). Disruptive actions are planned and confirmed first."
argument-hint: "<list|status|config|create|start|shutdown|stop|reboot|migrate|destroy> <vmid> [key=value ...]"
disable-model-invocation: true
---

# /proxmox:ct

Safety contract, tooling and workflow come from `proxmox:pve`; invoke it with the Skill tool if it is not loaded. Arguments: `$ARGUMENTS` = `<action> <vmid> [key=value ...]`. Extra `key=value` pairs are passed to the API call unchanged. Containers have no `reset`; use `/proxmox:vm` for QEMU guests.

## Resolve the container

- `list`: `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /cluster/resources type=vm`, keep `type == "lxc"`, print vmid, name, node, status, hastate, lock. Done.
- `create`: see the create workflow below; the VMID may be omitted.
- Every other action: same call, find the item with the given `vmid`. Stop if it does not exist or if `type` is `qemu`. Remember `node`.

## Free actions (run directly, then verify)

| Action | Call |
|---|---|
| status | `GET /nodes/<node>/lxc/<vmid>/status/current` |
| config | `GET /nodes/<node>/lxc/<vmid>/config` (`GET .../pending` for staged changes) |
| start | `POST /nodes/<node>/lxc/<vmid>/status/start` returns a UPID |
| create | workflow below; `POST /nodes/<node>/lxc` returns a UPID |

### Create workflow

1. Node: use `node=` from the arguments; otherwise ask which node (list them from `GET /nodes`).
2. VMID: use the given one, else `GET /cluster/nextid` (not a reservation; handle "already exists" by picking the next free id).
3. Template: `GET /nodes/<node>/storage` to find storages with `vztmpl` content, then `GET /nodes/<node>/storage/<storage>/content content=vztmpl`. Pick the volid that matches the requested distribution; ask if several match. Never invent a volid.
4. Credentials: a container needs `password` (min 5 characters) or `ssh-public-keys` (plain text key, not percent-encoded). Ask the user for one of them; never invent a password and never print one you were given.
5. Call: `POST /nodes/<node>/lxc vmid=<n> ostemplate=<volid> hostname=<name> rootfs=<storage>:<GiB> cores=<n> memory=<MiB> net0=name=eth0,bridge=<bridge>,ip=dhcp unprivileged=1 ssh-public-keys=<key>` (or `password=`), add `start=1` when the user wants it running, plus any extra pairs (`storage=`, `pool=`, `features=`, `ostype=`). Wait on the UPID.

## Gated actions (PLAN block first)

For each of these: read `status/current` (and `config` for destroy), then show the PLAN block from the safety contract (Target, Current state, Action with the exact call, Effect, Revert, "Reply yes to proceed"). Stop. Run the call only after the user says yes in a later message. The guard hook asks again when the command runs; that is expected.

| Action | Call | Notes |
|---|---|---|
| shutdown | `POST /nodes/<node>/lxc/<vmid>/status/shutdown` | graceful; `timeout=` and `forceStop=1` are documented only for QEMU (params UNVERIFIED for LXC) |
| stop | `POST /nodes/<node>/lxc/<vmid>/status/stop` | immediate kill |
| reboot | `POST /nodes/<node>/lxc/<vmid>/status/reboot` | `timeout=` is documented only for QEMU (params UNVERIFIED for LXC) |
| migrate | `POST /nodes/<node>/lxc/<vmid>/migrate target=<node> [restart=1 timeout= target-storage= bwlimit=]` | a running container cannot live-migrate: pass `restart=1`, which stops and restarts it |
| destroy | `DELETE /nodes/<node>/lxc/<vmid> [purge=1 destroy-unreferenced-disks=1 force=1]` | see below |

Destroy rules: the container should be stopped; `force=1` destroys even a running one, so prefer a separate gated `shutdown` first. Ask whether to pass `purge=1` (removes it from backup jobs, replication and HA; required when HA-managed or replicated) and `destroy-unreferenced-disks=1`. List `rootfs` and every `mpN` volume from `config` that will be deleted. There is no revert; name the newest backup from `GET /nodes/<node>/storage/<storage>/content content=backup vmid=<vmid>` when one exists, otherwise say that none exists.

## Verify and report

Pipe each UPID into `${CLAUDE_PLUGIN_ROOT}/scripts/pve-task.sh -`, re-read `status/current`, then report the call, the UPID, the `exitstatus:` line and the state after (workflow steps 5 and 6 of the pve skill).
