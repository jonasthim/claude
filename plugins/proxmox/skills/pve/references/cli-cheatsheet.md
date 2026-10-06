# PVE 9 CLI cheatsheet (SSH tier)

Condensed from Proxmox 9.x sources (pve-docs 9.2.13, pve-manager, qemu-server, pve-container,
pve-storage, pve-cluster, pve-ha-manager, pve-access-control); items marked UNVERIFIED were
not confirmed.

## Contents

1. When to use the SSH tier
2. pvesh
3. qm
4. pct
5. vzdump, qmrestore, pct restore
6. pvesm and pveam
7. pvecm
8. ha-manager
9. pvenode
10. pveum
11. Logs and diagnostics
12. pvesr

Run every command through `pve-ssh.sh`, e.g. `pve-ssh.sh -n pve1 qm list`. All these tools
run as root on a node; options accept both `-opt` and `--opt`. The gated list in SKILL.md
applies to the CLI exactly as to the API: `qm stop`, `pct destroy`, `pvesh delete`,
`ha-manager set`, `pvecm delnode` and friends need a PLAN block and a confirmation.

## 1. When to use the SSH tier

Prefer the API (`pve-api.sh`) because it works with a scoped token and returns structured
data. Use SSH only for:

- `ha-manager crm-command node-maintenance enable|disable <node>` (no REST endpoint)
- `apt dist-upgrade` (no REST endpoint; gated)
- `pvecm add|delnode|expected|qdevice` (gated)
- hand edits of `/etc/network/interfaces` followed by `ifreload -a` (apply is gated)
- `pct enter|exec|push|pull`, `pct console`
- `pvereport`, `pve8to9`, `pveversion -v`, `qm showcmd`, `journalctl`
- `qm guest exec` when the agent endpoint is not enough

## 2. pvesh

Subcommands map to HTTP methods: `get` GET, `set` PUT, `create` POST, `delete` DELETE, plus
`ls` and `usage`. Paths work with or without the `/api2/json` prefix. Must run as root on
the node.

```
pvesh get /cluster/resources --type vm --output-format json
pvesh get /nodes/{node}/qemu/{vmid}/config --current 1
pvesh get /nodes/{node}/qemu/{vmid}/status/current
pvesh get /cluster/status
pvesh get /cluster/nextid
pvesh usage /nodes/{node}/qemu/{vmid}/config -v --command set     # print the schema
pvesh usage /cluster/ha/rules -v --returns --command create
```

Options: `--output-format text|json|json-pretty|yaml`, `--human-readable`, `--noborder`,
`--noheader`, `--quiet`, `--noproxy`, `--nooutput`. `/cluster/resources` fields: id, type,
status, name, node, storage, pool, cpu, maxcpu, mem, maxmem, disk, maxdisk, uptime, hastate,
lock, tags, template, vmid. Use `pvesh usage <path> -v` whenever a parameter is unknown
rather than guessing it.

## 3. qm

Commands: `list`, `status`, `config`, `pending`, `showcmd [--pretty] [--snapshot]`;
`start|stop|reset|shutdown|reboot|suspend|resume <vmid>`; `clone <vmid> <newid>`;
`migrate <vmid> <target>`; `remote-migrate`; `snapshot|rollback|delsnapshot <vmid> <snap>`;
`listsnapshot <vmid>`; `template <vmid> [--disk]`; `destroy <vmid>`; `unlock`;
`wait <vmid> [--timeout]`; `terminal`; `guest cmd|exec|exec-status|passwd`;
`cloudinit dump <vmid> user|network|meta`, `cloudinit pending`, `cloudinit update`;
`importovf`; `import <vmid> <source>` (ESXi and similar); `enroll-efi-keys`.

Disk subcommands (old names remain as aliases):

| Current | Alias | Notes |
|---|---|---|
| `qm disk import <vmid> <source> <storage> [--format] [--target-disk scsi1]` | `importdisk` | docs prefer `--scsi0 STORAGE:0,import-from=/path/img` on `qm set` |
| `qm disk resize <vmid> <disk> <size>` | `resize` | `+10G` adds 10 GiB |
| `qm disk move <vmid> <disk> <storage>` | `move-disk` | gated |
| `qm disk rescan` | `rescan` | |
| `qm disk unlink` | | gated |

Key options:

- `clone`: `--full`, `--name`, `--target <node>` (shared storage only), `--storage`,
  `--format raw|qcow2|vmdk`, `--snapname`, `--pool`, `--description`, `--bwlimit`
- `migrate`: `--online`, `--with-local-disks`, `--targetstorage`, `--force` (root),
  `--migration_type secure|insecure`, `--migration_network CIDR`, `--bwlimit`
- `destroy`: `--purge` (removes from backup jobs, replication, HA),
  `--destroy-unreferenced-disks` (default 0), `--skiplock`
- `shutdown`: `--forceStop`, `--timeout`, `--keepActive`; `stop`: `--overrule-shutdown`,
  `--timeout`; `suspend`: `--todisk`, `--statestorage`
- `snapshot`: `--vmstate`, `--description`; `rollback`: `--start`
- `guest exec`: `--synchronous` (0 returns a pid), `--timeout` (0 disables),
  `--pass-stdin`; command after `--` (UNVERIFIED); needs `VM.GuestAgent.Unrestricted`
- `guest cmd` values: ping, info, get-time, get-timezone, get-host-name, get-osinfo,
  get-users, get-vcpus, get-fsinfo, get-memory-blocks, network-get-interfaces,
  fsfreeze-freeze|thaw|status, fstrim, shutdown, suspend-disk|ram|hybrid
- `--sshkeys` on the CLI takes a file path (qm reads and URI-escapes it); via pvesh or
  the API the value must already be percent-encoded. `--cipassword` prompts interactively.
- `qm set <vmid> -onboot 1`; `qm shutdown 300 && qm wait 300 -timeout 40`;
  `qm suspend ID --todisk`

The cloud-init recipe is in `cloud-init.md`.

## 4. pct

Commands: `list`, `config`, `pending`, `set`, `status [--verbose]`;
`create <vmid> <ostemplate>`; `restore <vmid> <archive>`;
`start|stop|shutdown|reboot|suspend|resume` (no reset); `clone <vmid> <newid>`;
`migrate <vmid> <target>`; `snapshot|rollback|delsnapshot|listsnapshot`;
`resize <vmid> <disk> <size>`; `move-volume`; `template`; `destroy`; `unlock`; `console`;
`enter`; `exec <vmid> -- cmd...`; `push <vmid> <file> <dest>`;
`pull <vmid> <path> <dest> [--user --group --perms]`; `fsck`, `mount`, `unmount`, `df`,
`rescan`, `fstrim`, `cpusets`.

`create` options: `--password` (min 5 chars, prompts on the CLI), `--ssh-public-keys FILE`,
`--storage` (default `local`), `--start`, `--pool`, `--force`, `--unique` (restore),
`--rootfs STORAGE:SIZE_GiB`,
`--net0 name=,bridge=,hwaddr=,ip=CIDR|dhcp,gw=,ip6=,gw6=,firewall=,tag=,mtu=,rate=`,
`--unprivileged` (new CTs default to 1), `--features nesting=1,keyctl=1,fuse=1,mount=...,mknod=1`,
`--ostype debian|devuan|ubuntu|centos|fedora|opensuse|archlinux|alpine|gentoo|nixos|unmanaged`.

- `clone`: `--full` (always for a normal CT; linked clones only from templates),
  `--hostname`, `--target` (shared storage), `--storage`, `--snapname`, `--pool`,
  `--bwlimit`. Full clone of a running container is only possible from a snapshot.
- `migrate`: `--restart`, `--timeout` (default 180), `--online`, `--target-storage`,
  `--bwlimit`. Running CTs cannot live-migrate; use `--restart`.
- `destroy`: `--purge`, `--force` (even if running), `--destroy-unreferenced-disks`.
- `exec`/`enter`: `--keep-env` defaults to 1; pass it explicitly.

```
pct create 100 local:vztmpl/debian-10.0-standard_10.0-1_amd64.tar.gz
pct set 100 -net0 name=eth0,bridge=vmbr0,ip=192.168.15.147/24,gw=192.168.15.1
pct move-volume 100 mp0 other-storage        # gated
pct destroy 100 --purge                      # gated
```

## 5. vzdump, qmrestore, pct restore

`vzdump <vmid...> [options]`:

- `--mode snapshot|suspend|stop` (default snapshot); `--compress 0|1|gzip|lzo|zstd`
  (default 0), `--zstd N`; `--storage ID` or `--dumpdir DIR`
- `--remove` (default 1) and `--prune-backups keep-last=,keep-hourly=,keep-daily=,keep-weekly=,keep-monthly=,keep-yearly=,keep-all=`
  (explicit `--remove 1` or `--prune-backups` is gated)
- `--notes-template '{{guestname}} on {{node}}'` (`{{cluster}}`, `{{guestname}}`, `{{node}}`, `{{vmid}}`);
  `--protected`; `--all`; `--exclude`; `--bwlimit KiB/s`; `--fleecing` (VMs)
- `--pbs-change-detection-mode legacy|data|metadata`;
  `--notification-mode auto|legacy-sendmail|notification-system` (`--mailto` deprecated)

Restore:

```
qmrestore <archive> <vmid> [--storage] [--unique] [--force] [--pool] [--bwlimit] [--live-restore] [--start]
pct restore <vmid> <archive> [--storage] [--unique] [--force]
qmrestore /mnt/backup/vzdump-qemu-888.vma 601
pct restore 600 /mnt/backup/vzdump-lxc-777.tar
pvesm list <storage> --content backup [--vmid N]      # list archives
```

`--live-restore` needs a PBS source. `--force` overwrites an existing guest (gated).

## 6. pvesm and pveam

`pvesm`: `status`; `list <storage> [--content] [--vmid]`;
`alloc <storage> <vmid> <filename> <size> [--format]`; `free <volume>` (gated);
`add <type> <storage>`; `set`; `remove` (gated); `scan nfs|cifs|iscsi|lvm|lvmthin|pbs|zfs`;
`path <volume>`; `extractconfig <volume>`; `export`; `import`; `prune-backups` (gated);
`apiinfo`.

There is no `pvesm download-url` command. Download via the API instead:

```
pvesh create /nodes/{node}/storage/{storage}/download-url --url URL --content iso|vztmpl|import \
  --filename NAME [--checksum X --checksum-algorithm sha256] [--verify-certificates 1]
```

(needs `Datastore.AllocateTemplate` plus `Sys.Audit`/`Sys.Modify` on `/` or
`Sys.AccessNetwork` on the node).

`pveam`: `update`; `available [--section system|mail|turnkeylinux]`;
`download <storage> <template>`; `list <storage>`; `remove <path>` (gated).
Example: `pveam download local debian-10.0-standard_10.0-1_amd64.tar.gz`.

## 7. pvecm

`create <name>`; `add <hostname> [--link0 IP] [--link1 IP] [--use_ssh] [--fingerprint] [--force]`;
`addnode`; `delnode <node>`; `status`; `nodes`; `expected <n>`;
`qdevice setup <addr> | remove`; `updatecerts`; `keygen`; `apiver`.

`create`, `add`, `delnode`, `expected` and `qdevice` are gated. `delnode` warning: power
the node off first; a removed node cannot rejoin without a reinstall. `pvecm status` and
`pvecm nodes` are free.

## 8. ha-manager

`status`; `config`; `add <sid>`; `set <sid> --state started|stopped|disabled|ignored`;
`remove <sid>`; `migrate <sid> <node>`; `relocate <sid> <node>`;
`rules list|config|add <type> <rule>|set|remove`;
`crm-command migrate|relocate <sid> <node>`; `crm-command stop <sid> <timeout>`;
`crm-command node-maintenance enable|disable <node>`; `crm-command disarm-ha freeze|ignore`;
`crm-command arm-ha` (9.2). `groupadd`/`groupset` still exist but groups are deprecated in 9;
use rules.

```
ha-manager rules add node-affinity ha-rule-vm100 --resources vm:100 --nodes node1
ha-manager rules add resource-affinity keep-together --affinity positive --resources vm:100,vm:200
```

Gated (confirm first): `remove`, `set`, `migrate`, `relocate`, `rules set|remove`, every
`crm-command`. Free: `status`, `config`, `add`, `rules list|config|add`.

## 9. pvenode

`config get|set`; `cert info|set|delete` (`pvenode cert set certificate.crt certificate.key -force`);
`acme ...`; `task list|status|log` (`pvenode task list --errors --vmid 100`);
`startall [--vms] [--force]`; `stopall` (gated); `migrateall <target> [--vms] [--with-local-disks] [--max-workers]` (gated);
`wakeonlan <node>`.

There is no `pvenode updates` command. Use the API:

```
pvesh create /nodes/{node}/apt/update      # refresh package lists (UPID)
pvesh get /nodes/{node}/apt/update         # list pending updates
pvesh get /nodes/{node}/apt/versions
```

Installing upgrades has no API: `apt dist-upgrade` over SSH, gated.

## 10. pveum

Groups: `user add|modify|delete|list|permissions`; `user token add|modify|delete|list|permissions`;
`group`; `role add|modify|delete|list`; `acl modify|delete|list`; `realm`; `pool`; `passwd`; `ticket`.

```
pveum user add testuser@pve -comment "description"
pveum user token add joe@pve monitoring -privsep 1
pveum role add CUSTOM_ROLE -privs "VM.PowerMgmt VM.Console"
pveum acl modify / -user joe@pve -role PVEAuditor
pveum acl modify /vms -group developers -role PVEVMAdmin
pveum acl modify /vms -token 'joe@pve!monitoring' -role PVEAuditor
```

- `acl modify <path> --roles <list> [--users] [--groups] [--tokens user@realm!tokenid] [--propagate 1] [--delete]`
- `user token add`: `--privsep` defaults on (the token needs its own ACLs); `--expire <epoch>`;
  `--comment`; prints `full-tokenid` and `value` once; the value cannot be retrieved later, so
  the user runs it in their own shell, never Claude.
- Roles, privileges and recipes: `permissions.md`.

## 11. Logs and diagnostics

- Task logs live under `/var/log/pve/tasks/`. API: `pvesh get /nodes/{node}/tasks [--limit] [--vmid] [--errors] [--typefilter vzdump] [--source active|archive|all] [--since] [--until]`;
  `pvesh get /nodes/{node}/tasks/{upid}/status`; `.../log`; stop with `pvesh delete /nodes/{node}/tasks/{upid}` (gated as DELETE).
  CLI: `pvenode task list|status|log`.
- `pveversion [-v]`; `qm showcmd <vmid> --pretty`; `pvereport`.
- Daemons: `pvedaemon`, `pveproxy`, `pvestatd`, `spiceproxy`, `pvescheduler`;
  `journalctl -eu pve-ha-crm`. Unit names `pve-cluster`, `pve-ha-lrm`, `corosync` UNVERIFIED.
  Restarting any of them is gated (`systemctl restart`).
- Upgrade path 8 to 9: latest 8.4 first, run `pve8to9`, then bookworm to trixie with
  deb822 `.sources` (`apt modernize-sources`).

## 12. pvesr

Storage replication jobs for guests on local ZFS storage. `status [--guest <vmid>]`; `list`;
`read <id>`; `create-local-job <vmid>-<n> <target> [--schedule '*/15'] [--rate <MB/s>]
[--comment]`; `update <id>`; `enable <id>`; `disable <id>`; `schedule-now <id>`;
`delete <id> [--keep] [--force]`.

- **`pvesr status` shows only the jobs whose source is the node it runs on**, the same jobs
  as `GET /nodes/N/replication` (confirmed on a 3-node PVE 9 cluster). For the whole cluster
  use `GET /cluster/replication` or read `/etc/pve/replication.cfg`; for health run `status`
  on every node. Whether `pvesr list` is cluster-wide is UNVERIFIED.
- Gated (confirm first): `delete` (also removes the replica on the target unless `--keep`),
  `disable`, `update --disable`. Free: `status`, `list`, `read`, `create-local-job`, `enable`,
  `schedule-now`, `update` of the schedule, rate or comment.
- Details and the coverage check: `cluster-ha.md`, "Storage replication".
