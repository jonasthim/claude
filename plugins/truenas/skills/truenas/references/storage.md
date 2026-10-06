# Storage: pools, datasets, zvols, snapshots, scrubs, quotas

Verify argument shapes with `tn.py methods <method> --schema`; the examples below are the
common 25.x forms.

## Pools

```
tn.py query pool --select name,status,healthy,warning,size,allocated,free,scan.function,scan.state,scan.end_time
tn.py call pool.get_instance 1                      # by id; includes topology
tn.py call pool.get_disks tank                      # disks in the pool (25.x: pool name or id)
tn.py call pool.is_upgraded 1                       # false => zpool feature upgrade pending
```

Health rules of thumb: `status` should be `ONLINE`, `healthy` true, `warning` false, no
`scan.errors`. Degraded topology shows in `topology.data[].children[].status`.
SSH gives the richest view: `zpool status -v tank`, `zpool list -o name,size,alloc,free,cap,frag,health`.

Mutations (all gated, all jobs, all need the user's explicit yes):
- `pool.create {name, topology:{data:[{type:"MIRROR"|"RAIDZ1"|..., disks:[...]}], ...}}` wipes disks.
- `pool.export id {destroy: false, cascade: true}` removes shares/tasks tied to the pool.
- `pool.attach`, `pool.replace`, `pool.detach`, `pool.offline`, `pool.online`, `pool.remove`,
  `pool.expand`, `pool.upgrade`.
- Import: `pool.import_find` (job, lists importable pools) then `pool.import_pool {guid}`.

## Scrubs

```
tn.py query pool.scrub                              # scheduled scrub tasks
tn.py call pool.scrub.run tank --job                # start a scrub now (threshold check)
tn.py call pool.scrub.scrub tank START --job        # START | STOP | PAUSE, no threshold check
tn.py query pool --select name,scan                 # progress: scan.percentage, scan.state
```

`pool.scrub.create {pool: <id>, threshold: 35, description, schedule:{minute,hour,dom,month,dow}, enabled}`.

## Datasets and zvols

```
tn.py query pool.dataset --select name,type,used.parsed,available.parsed,mountpoint,compression.value,encrypted,locked
tn.py query pool.dataset --filter name=tank/photos --extra '{"retrieve_children": false}'
tn.py call pool.dataset.details                     # UI-style summary incl. share/app usage
tn.py call pool.dataset.get_instance tank/photos
tn.py call pool.dataset.attachments tank/photos     # shares, apps, tasks that use it
tn.py call pool.dataset.processes tank/photos       # open file handles (why delete is EBUSY)
tn.py call pool.dataset.snapshot_count tank/photos
```

Create a filesystem dataset (not gated):

```
tn.py call pool.dataset.create '{"name": "tank/photos", "type": "FILESYSTEM",
  "compression": "LZ4", "atime": "OFF", "share_type": "SMB", "comments": "family photos"}'
```

- `share_type`: `GENERIC` (default), `SMB` (sets aclmode/acltype/casesensitivity for SMB),
  `APPS`, `MULTIPROTOCOL`. Pick it at create time; it is awkward to change later.
- Common properties: `compression`, `atime`, `recordsize`, `sync`, `deduplication` (avoid),
  `quota`, `refquota`, `reservation`, `refreservation`, `readonly`, `exec`, `snapdir`,
  `copies`, `aclmode`, `acltype`, `casesensitivity`. Values are the upper-case ZFS strings
  (`"ON"`, `"OFF"`, `"INHERIT"`, `"LZ4"`, `"ZSTD"`); sizes in bytes.
- Choices: `pool.dataset.compression_choices`, `pool.dataset.recordsize_choices`,
  `pool.dataset.checksum_choices`, `pool.dataset.encryption_algorithm_choices`.

Create a zvol (block device for iSCSI or VMs):

```
tn.py call pool.dataset.create '{"name": "tank/vm/disk0", "type": "VOLUME",
  "volsize": 107374182400, "volblocksize": "16K", "sparse": true}'
```

Update properties (not gated, but snapshot first if data layout changes):

```
tn.py call pool.dataset.update tank/photos '{"compression": "ZSTD", "comments": "..."}'
```

Delete (gated, job): `pool.dataset.delete tank/old {recursive: true, force: false}`.
`EBUSY` means something is attached: check `attachments` and `processes` first.

Encryption: `pool.dataset.encryption_summary`, `pool.dataset.lock` (gated), `pool.dataset.unlock
{datasets:[{name, passphrase}]}` (job), `pool.dataset.export_key` (gated: prints secrets).

## Quotas

```
tn.py call pool.dataset.get_quota tank/home USER          # USER | GROUP | DATASET | PROJECT
tn.py call pool.dataset.set_quota tank/home '[{"quota_type": "USER", "id": "alice", "quota_value": 53687091200}]'   # gated
```

Dataset-level quota/refquota go through `pool.dataset.update`.

## Snapshots

25.04 renamed `zfs.snapshot.*` to `pool.snapshot.*`. If `pool.snapshot` is missing in
`tn.py methods`, use `zfs.snapshot` with the same arguments.

```
tn.py query pool.snapshot --filter dataset=tank/photos --select name,properties.used.parsed,properties.creation.parsed --order-by=-properties.creation.parsed
tn.py query pool.snapshot --filter name~@auto- --limit 20
tn.py call pool.snapshot.create '{"dataset": "tank/photos", "name": "before-migration", "recursive": false}'
tn.py call pool.snapshot.create '{"dataset": "tank", "naming_schema": "manual-%Y-%m-%d_%H-%M", "recursive": true}'
```

Querying snapshots across a big pool is slow; filter by `dataset` or use
`--extra '{"properties": ["used", "creation"]}'` to limit properties. Over SSH:
`zfs list -t snapshot -r -o name,used,refer,creation -s creation tank/photos`.

Gated operations (ask, then `--confirm`):
- `pool.snapshot.delete "tank/photos@name" {defer: false, recursive: false}`
- `pool.snapshot.rollback "tank/photos@name" {recursive: false, force: false}` destroys
  every newer snapshot on that dataset; say so when asking.
- `pool.snapshot.clone {snapshot: "tank/photos@name", dataset_dst: "tank/photos-restore"}`
  is not gated: it is the safe way to recover files. Then `pool.dataset.promote` if the clone
  should outlive the snapshot.
- `pool.snapshot.hold` / `.release` protect a snapshot from deletion.

Periodic snapshot tasks:

```
tn.py query pool.snapshottask
tn.py call pool.snapshottask.create '{"dataset": "tank/photos", "recursive": true,
  "lifetime_value": 2, "lifetime_unit": "WEEK", "naming_schema": "auto-%Y-%m-%d_%H-%M",
  "schedule": {"minute": "0", "hour": "*/4", "dom": "*", "month": "*", "dow": "*"}, "enabled": true}'
tn.py call pool.snapshottask.run 3                 # run task id 3 now
```

Recovering a single file: mount point `/mnt/tank/photos/.zfs/snapshot/<snapname>/` is
browsable over SSH; copy from there instead of rolling back.

## Disks and SMART

```
tn.py query disk --select name,serial,model,size,type,pool,zfs_guid,bus
tn.py call disk.temperatures '["sda", "sdb"]'
tn.py query smart.test                              # scheduled SMART tests
tn.py call smart.test.results '[["disk", "=", "sda"]]'
tn.py call smart.test.manual_test '[{"identifier": "sda", "type": "SHORT"}]'   # SHORT | LONG
```

Over SSH: `smartctl -a /dev/sda`, `lsblk -o NAME,SIZE,SERIAL,MODEL`. Disk `name` can change
across reboots; identify disks by `serial` or `zfs_guid` when talking to the user.
`disk.wipe` is gated and destroys data.
