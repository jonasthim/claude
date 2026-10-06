---
name: snapshot
description: "Slash command: list, create, roll back or delete snapshots of a Proxmox VE 9 VM or LXC container. Create runs freely; rollback and delete are planned and confirmed first."
argument-hint: "<vmid> <list|create|rollback|delete> [snapname] [key=value ...]"
disable-model-invocation: true
---

# /proxmox:snapshot

If the `proxmox:pve` skill is not loaded in this conversation, invoke it with the Skill tool and apply its safety contract.

Arguments: `$ARGUMENTS` = `<vmid> <action> [snapname] [key=value ...]`. Use `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh` for calls and `${CLAUDE_PLUGIN_ROOT}/scripts/pve-task.sh <UPID>` to wait; only `OK` or `WARNINGS` count as success. Never print `PVE_TOKEN_SECRET`.

## Step 1: resolve the guest

`${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /cluster/resources type=vm`, find the item with the given `vmid`, remember `node` and `type` (`qemu` or `lxc`). Stop with a clear message if the VMID does not exist. Below, `<kind>` is `qemu` or `lxc`; snapshot endpoints are the same for both.

## list (free)

`${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes/<node>/<kind>/<vmid>/snapshot`. Print each entry's `name` and `description` plus any parent, time or vmstate fields the response contains (exact field names UNVERIFIED; do not invent them). Optional: `GET /nodes/<node>/<kind>/<vmid>/snapshot/<snapname>/config` for one snapshot's config.

## create (free)

1. `snapname` is required: ask if missing. Use only letters, digits, hyphens and underscores.
2. `POST /nodes/<node>/<kind>/<vmid>/snapshot snapname=<name> [description=<text>] [vmstate=1]`. `vmstate=1` (QEMU only) also saves RAM so a rollback resumes the running state; it is slower and needs space on the state storage. Snapshots need snapshot-capable storage for every disk; a 500 error naming a volume means that storage cannot snapshot.
3. Wait on the UPID, then list the snapshots again and confirm the new entry.

## rollback (gated)

1. Read `GET .../snapshot` and `GET .../status/current`.
2. Show the PLAN block from the safety contract: Target `<vmid> on <node>`; Current state (running/stopped, snapshots newer than the target); Action `POST /nodes/<node>/<kind>/<vmid>/snapshot/<snapname>/rollback [start=1]`; Effect: discards every change to disks (and RAM when `vmstate` was saved) made since `<snapname>`, and a running guest is stopped first; Revert: none, unless the user first creates a new snapshot of the current state (offer to do that as a free step); "Reply yes to proceed".
3. Stop. Run the rollback only after the user says yes in a later message. The guard hook asks once more when the command runs; that is expected.
4. Wait on the UPID, read `status/current`, report.

## delete (gated)

1. Read `GET .../snapshot` and find the snapshot and its children.
2. Show the PLAN block: Target; Current state (snapshot date, description, whether other snapshots depend on it); Action `DELETE /nodes/<node>/<kind>/<vmid>/snapshot/<snapname> [force=1]`; Effect: the snapshot is removed and its disk state merged forward, the guest keeps running; Revert: none; "Reply yes to proceed". Mention `force=1` only when a previous delete left the snapshot in an inconsistent state.
3. Stop. Run the delete only after the user says yes in a later message.
4. Wait on the UPID, list snapshots again, report.

## Report

For every call: the exact call, the UPID, the `exitstatus:` line from pve-task.sh and the snapshot list after. If a task fails, show its log tail and do not retry a gated action on your own.
