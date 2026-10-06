# Backups and restores (vzdump)

Condensed from Proxmox 9.x sources (pve-guest-common, vzdump.adoc, qmrestore, pve-manager
backup jobs); items marked UNVERIFIED were not confirmed.

## Run a backup

```
pve-api.sh POST /nodes/N/vzdump vmid=100 storage=S mode=snapshot compress=zstd remove=0 | pve-task.sh -
```

Parameters: `vmid` (list), `all`, `exclude`, `pool`, `mode snapshot|suspend|stop`,
`compress 0|1|gzip|lzo|zstd`, `storage`, `remove` (default 1), `prune-backups`,
`notes-template`, `protected`, `notification-mode`, `bwlimit`, `fleecing`, `job-id`,
`pbs-change-detection-mode`. CLI: `vzdump <vmid...>` with the same names as `--opts`, plus
`--dumpdir DIR` and `--zstd N`.

Modes and trade-offs:

| Mode | Consistency | Downtime | Notes |
|---|---|---|---|
| `stop` | most consistent | guest is stopped for the backup | |
| `snapshot` | lower than stop | least downtime | default; CT needs snapshot-capable storage on all volumes |
| `suspend` | between the two | guest suspended | CT suspend mode uses rsync |

`remove`/`prune-backups` semantics: `remove` defaults to 1 and removes older backups
according to the configured retention (which settings apply is UNVERIFIED). Pass
`remove=0` for a plain one-off backup.
An explicit `remove=1` or a `prune-backups=keep-last=N,...` parameter deletes older
archives and is gated. `prune-backups` keys: `keep-last`, `keep-hourly`, `keep-daily`,
`keep-weekly`, `keep-monthly`, `keep-yearly`, `keep-all`.

Other options:

- `notes-template='{{guestname}} on {{node}}'` (variables `{{cluster}}`, `{{guestname}}`,
  `{{node}}`, `{{vmid}}`) labels the archive.
- `protected=1` marks the archive as protected (pruning behaviour UNVERIFIED).
- `fleecing` (VMs only).
- `pbs-change-detection-mode legacy|data|metadata` (PBS targets).
- `notification-mode auto|legacy-sendmail|notification-system` (`mailto` is deprecated).

## Backup jobs (need Sys.Modify on /)

```
pve-api.sh GET /cluster/backup
pve-api.sh GET /cluster/backup/ID
pve-api.sh GET /cluster/backup/ID/included_volumes
pve-api.sh POST /cluster/backup id=nightly-web schedule="02:00" vmid=100,101 storage=S mode=snapshot compress=zstd prune-backups=keep-daily=7,keep-weekly=4 notes-template="{{guestname}} on {{node}}"
pve-api.sh PUT /cluster/backup/ID enabled=0        # `enabled` key UNVERIFIED
pve-api.sh DELETE /cluster/backup/ID               # gated
```

`schedule` is a calendar event (systemd-like, e.g. `02:00`, `sat 03:00`; exact grammar
not in the notes). Job creation is free; setting a job's prune options does not delete
anything until the next run.

## List archives

```
pve-api.sh GET /nodes/N/storage/S/content content=backup
pve-api.sh GET /nodes/N/storage/S/content content=backup vmid=100
pve-ssh.sh -n N pvesm list S --content backup --vmid 100
pve-ssh.sh -n N pvesm extractconfig S:backup/vzdump-qemu-100-....vma.zst
```

Volids look like `S:backup/vzdump-qemu-100-<timestamp>.vma.zst` (VM) or
`S:backup/vzdump-lxc-105-<timestamp>.tar.zst` (CT); file name pattern UNVERIFIED beyond
the `vzdump-qemu-`/`vzdump-lxc-` prefix and the CLI examples (`.vma`, `.tar`).

## Restore

Restore to a new vmid (free) or over an existing guest (`force=1`; gated, it replaces the
guest and its disks).

```
# VM
pve-api.sh POST /nodes/N/qemu vmid=100 archive=S:backup/vzdump-qemu-100-....vma.zst storage=S2 unique=1 | pve-task.sh -
pve-api.sh POST /nodes/N/qemu vmid=100 archive=S:backup/... force=1 | pve-task.sh -      # gated
# CT
pve-api.sh POST /nodes/N/lxc vmid=105 ostemplate=S:backup/vzdump-lxc-105-....tar.zst restore=1 storage=S2 | pve-task.sh -
pve-api.sh POST /nodes/N/lxc vmid=105 ostemplate=S:backup/... restore=1 force=1 | pve-task.sh -   # gated
```

- `unique=1` gives the restored guest new unique properties such as MAC addresses
  (detail UNVERIFIED); needs `restore` for CT, `archive` for VM.
- `live-restore=1` (VM, PBS source).
- `storage` picks the target storage for all disks.
- Restoring over an existing guest needs VM.Backup; a new vmid needs VM.Allocate and
  Datastore.AllocateSpace.
- CLI: `qmrestore <archive> <vmid> [--storage] [--unique] [--force] [--pool] [--bwlimit] [--live-restore] [--start]`;
  `pct restore <vmid> <archive> [--storage] [--unique] [--force]`.

## Diagnose a failed backup

```
pve-api.sh GET /nodes/N/tasks typefilter=vzdump errors=1 limit=10
pve-api.sh GET /nodes/N/tasks typefilter=vzdump since=... until=...
pve-api.sh GET /nodes/N/tasks/UPID/status
pve-api.sh GET /nodes/N/tasks/UPID/log start=0 limit=500
pve-api.sh GET /nodes/N/storage/S/status                  # space left on the target
pve-api.sh GET /cluster/backup                            # which job, which guests, which storage
```

Read the task log from the end; vzdump prints one `INFO`/`ERROR` block per guest, so a
multi-guest job can be partly successful (`exitstatus` then reads `WARNINGS: n` or names
the failed vmid; exact wording UNVERIFIED). Common causes to check, in order: target storage
full or unreachable (`storage/S/status`), guest locked by another task (`lock` in
`status/current`), snapshot mode on a CT volume without snapshot support (switch that guest
to `mode=suspend` or `stop`), a guest that was migrated to another node after the job was
defined. Proposed fixes (freeing space, changing the job's mode, re-running the backup
with `remove=0`) are free; deleting archives to make room is gated.
