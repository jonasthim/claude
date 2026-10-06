# System: alerts, services, updates, boot environments, replication, cloud sync, tasks

## Alerts

```
tn.py query alert --filter dismissed=false --select uuid,level,klass,formatted,datetime,last_occurrence
tn.py call alert.list                                 # everything incl. dismissed
tn.py call alert.list_categories                      # classes grouped by category
tn.py call alert.dismiss "<uuid>"                     # not gated
tn.py call alert.restore "<uuid>"
tn.py call alertclasses.config                        # per-class level overrides / policy
tn.py query alertservice                              # email/slack/etc destinations
```

Levels, low to high: `INFO`, `NOTICE`, `WARNING`, `ERROR`, `CRITICAL`, `ALERT`, `EMERGENCY`.
`klass` names the check (e.g. `ScrubPaused`, `SMART`, `VolumeStatus`, `ZpoolCapacityWarning`,
`AppUpdate`, `CertificateIsExpiring`). `formatted` is the human text; quote it to the user.
Dismissing hides an alert until it fires again; it does not fix anything.

## Services

```
tn.py query service --select id,service,state,enable,pids
tn.py call service.started cifs
tn.py call service.start cifs                         # start | stop (gated) | restart | reload
tn.py call service.restart nfs
tn.py call service.update cifs '{"enable": true}'      # start on boot
```

Service names: `cifs` (SMB), `nfs`, `iscsitarget`, `ssh`, `smartd`, `snmp`, `ups`, `ftp`,
`webdav` (removed in 25.x), `nut`. Config lives in `<name>.config` / `<name>.update`
(`smb.config`, `nfs.config`, `ssh.config`, `smartd.config`, `ups.config`); the `update`
calls are gated for `smb`, `nfs`, `iscsi.global`, `system.general`, `system.advanced`.

## Updates

```
tn.py call update.get_trains                          # current train and choices
tn.py call update.check_available                     # {status: "AVAILABLE"|"UNAVAILABLE"|..., version, changelog}
tn.py call update.get_pending                         # downloaded but not applied
tn.py call update.config                              # autocheck setting
tn.py call update.status                              # 25.10+: richer status object
```

Applying an update is gated and reboots the system when `reboot: true`:

```
tn.py call update.download --job --confirm
tn.py call update.update '{"reboot": false}' --job --confirm      # install to a new boot env, reboot later
tn.py call update.update '{"train": "TrueNAS-SCALE-Goldeye", "reboot": true}' --job --confirm
```

Before an update: check `pool.query` health, `alert` list, running jobs, and tell the user
apps and shares go down during the reboot. After: `system.version`, `boot.environment.query`.
If the websocket drops mid-update, that is expected; reconnect with `tn.py info` after a few
minutes.

25.04 renamed `bootenv.*` to `boot.environment.*`:

```
tn.py query boot.environment --select id,active,activated,created,used_bytes,keep
tn.py call boot.environment.activate '{"id": "25.04.2"}' --confirm
tn.py call boot.environment.keep '{"id": "...", "value": true}'
tn.py call boot.get_state                             # boot pool health
```

Reboot/shutdown (gated): `system.reboot "<reason>" '{"delay": 0}'`, `system.shutdown "<reason>"`.
Check `methods system.reboot --schema`: 25.04 added the reason argument.

## Replication (ZFS send/receive)

```
tn.py query replication --select id,name,direction,transport,source_datasets,target_dataset,auto,enabled,state
tn.py call replication.get_instance 1                 # state.state, state.datetime, state.error, state.last_snapshot
tn.py query keychaincredential --select id,name,type  # SSH_KEY_PAIR / SSH_CREDENTIALS used by remote tasks
tn.py call replication.list_datasets SSH 1            # datasets visible via credential id 1
```

Running a task is gated because a PUSH with `retention_policy: SOURCE` can delete target
snapshots and a PULL can overwrite local data:

```
tn.py call replication.run 1 --job --confirm
tn.py call replication.run_onetime '{...full task definition..., "exclude_mountpoint_property": true}' --job --confirm
```

Create (not gated; nothing runs until `auto`/schedule or an explicit run):

```
tn.py call replication.create '{"name": "photos to backup", "direction": "PUSH", "transport": "SSH",
  "ssh_credentials": 1, "source_datasets": ["tank/photos"], "target_dataset": "backup/photos",
  "recursive": true, "periodic_snapshot_tasks": [3], "auto": true,
  "retention_policy": "SOURCE", "enabled": true}'
```

- `transport`: `SSH`, `SSH+NETCAT` (faster, needs open ports), `LOCAL` (same box, no credential).
- Use `also_include_naming_schema: ["auto-%Y-%m-%d_%H-%M"]` when not tied to a snapshot task.
- `retention_policy`: `SOURCE` mirrors deletions, `CUSTOM` with `lifetime_value/unit`, `NONE` keeps all.
- Failures surface in `state.error` and as alerts (`ReplicationFailed`). Common causes: SSH
  key not accepted, target dataset has newer snapshots (`replication.target_unmatched_snapshots`
  shows them), or encryption mismatch (`encryption` / `properties: false`).

## Cloud sync, rsync, cron

```
tn.py query cloudsync --select id,description,direction,transfer_mode,path,enabled,job
tn.py call cloudsync.sync 2 --job --confirm            # gated: SYNC mode deletes on the destination
tn.py call cloudsync.sync 2 '{"dry_run": true}' --job  # dry run still gated by name; explain and ask
tn.py query rsynctask
tn.py call rsynctask.run 1 --job --confirm
tn.py query cronjob --select id,description,command,user,schedule,enabled
tn.py call cronjob.run 1 --job                        # not gated
```

`transfer_mode`: `SYNC` (mirror, deletes), `COPY` (add/overwrite only), `MOVE`. Mention the
mode when asking to run a task.

## System info and logs

```
tn.py call system.info
tn.py call system.general.config                      # ui_port, ui_httpsport, timezone, ui_certificate
tn.py call system.advanced.config
tn.py call network.general.summary                    # ips, default routes, nameservers
tn.py query interface --select name,type,state.link_state,aliases
tn.py call reporting.netdata_get_data '[{"name": "cpu"}]' '{"unit": "HOUR"}'   # check --schema
```

SSH:
```
journalctl -u middlewared -n 300 --no-pager            # middleware log
tail -n 200 /var/log/middlewared.log
journalctl -b -p err --no-pager                        # kernel/system errors this boot
dmesg -T | tail -n 100
zpool iostat -v 2 5
top -bn1 | head -20
```

Network, interface, static route, certificate, user and group changes are gated namespaces:
explain the change and the rollback path (interfaces: `interface.commit` then
`interface.checkin` within the timeout, or the box reverts) before asking for confirmation.
