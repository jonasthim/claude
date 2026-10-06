# PVE 9 REST API cheatsheet

Condensed from Proxmox 9.x sources (pve-http-server, pve-manager, qemu-server, pve-container,
pve-storage, pve-access-control, pve-ha-manager, pve-network, pve-firewall, pve-docs); items
marked UNVERIFIED were not confirmed.

## Contents

1. Auth
2. Envelope and HTTP codes
3. Async tasks (UPID)
4. Cluster and nodes
5. QEMU VMs
6. LXC containers
7. Backups
8. Storage
9. Node network
10. SDN
11. Firewall
12. HA
13. Access control
14. Cloud-init keys
15. PVE 9.x additions

Endpoint line format: `METHOD path - params - privilege` (`none` = no params). Paths are relative to
`https://HOST:8006/api2/json`. `n/a` in the privilege column means the notes do not state
it; find it with `pvesh usage <path> -v` on a node or from the 403 message. `(UPID)` marks
calls whose `data` is a UPID string to poll with `pve-task.sh`.

## 1. Auth

- pveproxy listens on port 8006. Base URL `https://HOST:8006/api2/json/<path>`.
- Use `/api2/json` (real HTTP status codes). `/api2/extjs` always returns 200 with
  `success:0|1`, which hides errors from exit-code checks.
- API token header, verbatim: `Authorization: PVEAPIToken=USER@REALM!TOKENID=UUID`. The
  server splits at the last `=`. No CSRF token needed with API tokens.
- Expired token error text: `token '<id>' access expired`.
- `privsep=1` (default) tokens need their own ACLs; effective permissions are the
  intersection of user and token. `privsep=0` tokens equal the user.
- Ticket flow (interactive use only): `POST /access/ticket` with `username`, `password`
  (optional `realm`, `otp`) returns `username`, `ticket`, `CSRFPreventionToken`, `cap`. Send
  `Cookie: PVEAuthCookie=<ticket>`; every non-GET also needs header
  `CSRFPreventionToken: <token>`. Ticket lifetime 2 h. Auth failures are delayed 3 s.

## 2. Envelope and HTTP codes

- Success body: `{"data": <result>}` (sometimes with `total`, `changes`).
- Error body: `{"data":null,"message":"...","errors":{"<param>":"<msg>"}}` with the real status.

| Code | Meaning |
|---|---|
| 200 | ok |
| 400 | parameter validation failed (`errors` names the parameter) |
| 401 | missing or invalid auth |
| 403 | permission check failed |
| 500 | uncaught server error, e.g. `VM already running` |
| 501 | not implemented, `no such uri`, or a body sent with GET/DELETE |
| 506 | bad upload content type |

Bodies: POST/PUT accept `application/x-www-form-urlencoded` or `application/json` (set
`Content-Type`). Query parameters override body parameters. GET/DELETE must not carry a body.

## 3. Async tasks (UPID)

- Format: `UPID:<node>:<pid hex8>:<pstart hex>:<starttime hex8>:<type>:<id>:<user>:`.
  The node is the second field; URL-encode the UPID in paths (it contains `:`); strict
  need to encode UNVERIFIED, the scripts always encode.
- `GET /nodes/{node}/tasks/{upid}/status - none - n/a` returns `status` (`running`|`stopped`)
  and `exitstatus` (`OK`, `WARNINGS: n`, or error text). Only `OK` and `WARNINGS` are success.
- `GET /nodes/{node}/tasks/{upid}/log - start, limit, download=1 for raw text - n/a`
  returns `[{n, t}]`.
- `DELETE /nodes/{node}/tasks/{upid} - none - n/a` stops a running task.
- `GET /nodes/{node}/tasks - start, limit, userfilter, typefilter, vmid, errors, source - n/a`
- `GET /cluster/tasks - none - n/a`

Calls that return a UPID: qemu/lxc create, destroy, start, stop, shutdown, reboot, reset
(qemu), suspend, resume, clone, migrate, resize, snapshot create/rollback/delete, template,
`POST .../config`; `POST /nodes/{node}/vzdump`; `PUT /nodes/{node}/network`;
`POST /nodes/{node}/apt/update`. `PUT .../qemu/{vmid}/config` is synchronous (returns null).

## 4. Cluster and nodes

- `GET /cluster/status - none - Sys.Audit on /` (item shape UNVERIFIED)
- `GET /cluster/resources - type=vm|storage|node|sdn - any authenticated user, filtered`
  (item `type` values: node, storage, pool, qemu, lxc, sdn, network; fields include id,
  type, status, name, node, storage, pool, cpu, maxcpu, mem, maxmem, disk, maxdisk, uptime,
  hastate, lock, tags, template, vmid)
- `GET /cluster/nextid - vmid=N (check one id) - any authenticated user` (not a
  reservation; 400 if taken)
- `GET /cluster/log - max=N - n/a`
- `GET /nodes - none - n/a`
- `GET /nodes/{node}/status - none - n/a`
- `POST /nodes/{node}/status - command=reboot|shutdown - Sys.PowerMgmt on /nodes/{node}` (gated)
- `POST /nodes/{node}/startall - none - n/a`
- `POST /nodes/{node}/stopall - none - n/a` (gated)
- `POST /nodes/{node}/suspendall - none - n/a` (gated)
- `POST /nodes/{node}/migrateall - none - n/a` (gated)
- `GET /nodes/{node}/syslog - none - n/a`
- `GET /nodes/{node}/journal - none - n/a`
- `GET /nodes/{node}/apt/update - none - n/a` (lists pending updates)
- `POST /nodes/{node}/apt/update - (UPID) - Sys.Modify on /nodes/{node}` (refreshes lists)
- `GET /nodes/{node}/apt/versions - none - Sys.Audit`
- `GET /nodes/{node}/apt/changelog - none - n/a`
- `GET /nodes/{node}/apt/repositories - none - n/a`
- `POST /cluster/bulk-action/guest/start - vms, timeout, max-workers - n/a` (9.x)
- `POST /cluster/bulk-action/guest/shutdown - vms, timeout, max-workers - n/a` (9.x, gated)
- `POST /cluster/bulk-action/guest/suspend - vms, timeout, max-workers - n/a` (9.x, gated)
- `POST /cluster/bulk-action/guest/migrate - vms, timeout, max-workers - n/a` (9.x, gated)

No API endpoint upgrades packages (SSH: `apt dist-upgrade`). No API endpoint for node
maintenance mode (SSH: `ha-manager crm-command node-maintenance enable|disable <node>`).

## 5. QEMU VMs

Prefix `/nodes/{node}/qemu`. Privileges are checked on `/vms/{vmid}` unless noted.

- `GET /nodes/{node}/qemu - none - n/a`
- `POST /nodes/{node}/qemu - vmid (required), config keys (name, memory, cores, net0, scsi0, scsihw, ostype, ...), archive (restore), storage, force, unique, live-restore, pool, bwlimit, start (default 0), ha-managed (UPID) - VM.Allocate + Datastore.AllocateSpace on the storage + SDN.Use on the bridge; restore over an existing guest: VM.Backup`
  (`force` and `unique` only with `archive`)
- `GET /nodes/{node}/qemu/{vmid}/config - current, snapshot - VM.Audit` (includes `digest`; pending values shown unless `current=1`)
- `PUT /nodes/{node}/qemu/{vmid}/config - config keys, delete, revert, digest - VM.Config.* by key` (synchronous)
- `POST /nodes/{node}/qemu/{vmid}/config - config keys, delete, revert, digest, skiplock, force (UPID) - VM.Config.* by key`
- `GET /nodes/{node}/qemu/{vmid}/pending - none - VM.Audit` (items `{key, value, pending, delete}`)
- `GET /nodes/{node}/qemu/{vmid}/status/current - none - VM.Audit`
- `POST /nodes/{node}/qemu/{vmid}/status/start - timeout, machine, targetstorage (UPID) - VM.PowerMgmt`
- `POST /nodes/{node}/qemu/{vmid}/status/stop - timeout, overrule-shutdown, keepActive (UPID) - VM.PowerMgmt` (gated)
- `POST /nodes/{node}/qemu/{vmid}/status/shutdown - timeout, forceStop, keepActive (UPID) - VM.PowerMgmt` (gated)
- `POST /nodes/{node}/qemu/{vmid}/status/reboot - timeout (UPID) - VM.PowerMgmt` (gated)
- `POST /nodes/{node}/qemu/{vmid}/status/reset - (UPID) - VM.PowerMgmt` (gated)
- `POST /nodes/{node}/qemu/{vmid}/status/suspend - todisk, statestorage (UPID) - VM.PowerMgmt` (gated)
- `POST /nodes/{node}/qemu/{vmid}/status/resume - (UPID) - VM.PowerMgmt`
- `POST /nodes/{node}/qemu/{vmid}/clone - newid (required), name, description, pool, snapname, storage (full only), format raw|qcow2|vmdk (full only), full, target (shared storage), bwlimit (UPID) - VM.Clone on source + VM.Allocate on /vms/{newid} or the pool`
  (templates clone linked by default; `full=1` for an independent copy)
- `DELETE /nodes/{node}/qemu/{vmid} - purge, destroy-unreferenced-disks (default 0), skiplock (root) (UPID) - VM.Allocate` (gated; fails for HA/replicated guests unless `purge=1`)
- `PUT /nodes/{node}/qemu/{vmid}/resize - disk, size (+N or absolute with K|M|G|T), digest (UPID) - VM.Config.Disk`
- `GET /nodes/{node}/qemu/{vmid}/migrate - none - VM.Migrate` (preconditions)
- `POST /nodes/{node}/qemu/{vmid}/migrate - target (required), online, force, migration_type secure|insecure, migration_network, with-local-disks, targetstorage, bwlimit, with-conntrack-state (UPID) - VM.Migrate` (gated)
- `POST /nodes/{node}/qemu/{vmid}/move_disk - (UPID) - n/a` (gated; params not in notes)
- `POST /nodes/{node}/qemu/{vmid}/template - disk (UPID) - VM.Allocate` (gated, irreversible)
- `GET /nodes/{node}/qemu/{vmid}/snapshot - none - VM.Audit`
- `POST /nodes/{node}/qemu/{vmid}/snapshot - snapname, vmstate, description (UPID) - VM.Snapshot`
- `POST /nodes/{node}/qemu/{vmid}/snapshot/{snap}/rollback - start (UPID) - VM.Snapshot or VM.Snapshot.Rollback` (gated)
- `DELETE /nodes/{node}/qemu/{vmid}/snapshot/{snap} - force (UPID) - VM.Snapshot` (gated)
- `GET /nodes/{node}/qemu/{vmid}/snapshot/{snap}/config - none - VM.Audit`
- `PUT /nodes/{node}/qemu/{vmid}/snapshot/{snap}/config - none - n/a`
- `GET /nodes/{node}/qemu/{vmid}/cloudinit - none - n/a` (pending cloud-init changes)
- `PUT /nodes/{node}/qemu/{vmid}/cloudinit - none - n/a` (regenerates the drive)
- `GET /nodes/{node}/qemu/{vmid}/cloudinit/dump - type=user|network|meta - n/a`
- `POST /nodes/{node}/qemu/{vmid}/agent/... - none - n/a` (guest exec needs VM.GuestAgent.Unrestricted)
- `POST /nodes/{node}/qemu/{vmid}/monitor - none - Sys.Audit/Sys.Modify in 9.x` (VM.Monitor removed)

## 6. LXC containers

Prefix `/nodes/{node}/lxc`. No `reset` endpoint for containers.

- `GET /nodes/{node}/lxc - none - n/a`
- `POST /nodes/{node}/lxc - vmid, ostemplate (required; template volid or backup volid), password (min 5), storage, force, restore (=1 for restore), unique (needs restore), pool, ssh-public-keys (plain text), bwlimit, start, ha-managed, config keys (hostname, rootfs, net0, cores, memory, unprivileged, ...) (UPID) - VM.Allocate + Datastore.AllocateSpace on storage + SDN.Use on bridge; restore over existing: VM.Backup`
- `GET /nodes/{node}/lxc/{vmid}/config - none - VM.Audit` (includes `digest`)
- `PUT /nodes/{node}/lxc/{vmid}/config - config keys, delete, revert, digest - VM.Config.* by key`
- `GET /nodes/{node}/lxc/{vmid}/pending - none - VM.Audit`
- `GET /nodes/{node}/lxc/{vmid}/status/current - none - VM.Audit`
- `POST /nodes/{node}/lxc/{vmid}/status/start - (UPID) - VM.PowerMgmt`
- `POST /nodes/{node}/lxc/{vmid}/status/stop - (UPID) - VM.PowerMgmt` (gated)
- `POST /nodes/{node}/lxc/{vmid}/status/shutdown - (UPID) - VM.PowerMgmt` (gated)
- `POST /nodes/{node}/lxc/{vmid}/status/reboot - (UPID) - VM.PowerMgmt` (gated)
- `POST /nodes/{node}/lxc/{vmid}/status/suspend - (UPID) - VM.PowerMgmt` (gated)
- `POST /nodes/{node}/lxc/{vmid}/status/resume - (UPID) - VM.PowerMgmt`
- `POST /nodes/{node}/lxc/{vmid}/clone - newid, hostname, description, pool, snapname, storage, full, target, bwlimit (UPID) - VM.Clone + VM.Allocate on /vms/{newid}`
  (full clone of a running CT only from a snapshot; linked clones only from templates)
- `DELETE /nodes/{node}/lxc/{vmid} - force, purge, destroy-unreferenced-disks (UPID) - VM.Allocate` (gated)
- `PUT /nodes/{node}/lxc/{vmid}/resize - disk, size, digest (UPID) - VM.Config.Disk`
- `POST /nodes/{node}/lxc/{vmid}/migrate - target, target-storage, online, restart, timeout (default 180), bwlimit (UPID) - VM.Migrate` (gated; running CTs need `restart=1`)
- `POST /nodes/{node}/lxc/{vmid}/move_volume - (UPID) - n/a` (gated; params not in notes)
- `POST /nodes/{node}/lxc/{vmid}/template - (UPID) - VM.Allocate` (gated)
- `GET /nodes/{node}/lxc/{vmid}/interfaces - none - n/a`
- Snapshots: same paths and params as QEMU under `/nodes/{node}/lxc/{vmid}/snapshot`.

## 7. Backups

- `POST /nodes/{node}/vzdump - vmid (list), all, exclude, pool, mode snapshot|suspend|stop, compress 0|1|gzip|lzo|zstd, storage, remove (default 1), prune-backups, notes-template, protected, notification-mode, bwlimit, fleecing, job-id, pbs-change-detection-mode (UPID) - n/a`
  (`remove` defaults to 1: pass `remove=0` unless the user wants pruning; explicit
  `remove=1` or `prune-backups=` is gated)
- `GET /cluster/backup - none - Sys.Modify on /` (jobs)
- `POST /cluster/backup - id, schedule (calendar event), vzdump params - Sys.Modify on /`
- `GET /cluster/backup/{id} - none - Sys.Modify on /`
- `PUT /cluster/backup/{id} - vzdump params - Sys.Modify on /`
- `DELETE /cluster/backup/{id} - none - Sys.Modify on /` (gated)
- `GET /cluster/backup/{id}/included_volumes - none - Sys.Modify on /`
- `GET /nodes/{node}/storage/{storage}/content - content=backup, vmid - n/a` (list backups)
- Restore VM: `POST /nodes/{node}/qemu - vmid, archive=<volid>, storage, force=1, unique, live-restore (UPID) - VM.Backup when overwriting`
- Restore CT: `POST /nodes/{node}/lxc - vmid, ostemplate=<backup volid>, restore=1, storage, force, unique (UPID) - VM.Backup when overwriting`
- `POST /nodes/{node}/storage/{storage}/prunebackups - none - n/a` (gated; params not in notes)

## 8. Storage

- `GET /storage - none - n/a`
- `POST /storage - none - n/a`
- `GET /storage/{storage} - none - n/a`
- `PUT /storage/{storage} - none - n/a`
- `DELETE /storage/{storage} - none - n/a` (gated)
- `GET /nodes/{node}/storage - storage, content, enabled, target, format - n/a`
- `GET /nodes/{node}/storage/{storage}/status - none - n/a`
- `GET /nodes/{node}/storage/{storage}/content - content, vmid - n/a`
- `POST /nodes/{node}/storage/{storage}/content - (alloc) - n/a`
- `GET /nodes/{node}/storage/{storage}/content/{volume} - none - n/a`
- `PUT /nodes/{node}/storage/{storage}/content/{volume} - none - n/a`
- `POST /nodes/{node}/storage/{storage}/content/{volume} - none - n/a`
- `DELETE /nodes/{node}/storage/{storage}/content/{volume} - none - n/a` (gated)
- `POST /nodes/{node}/storage/{storage}/upload - multipart/form-data: content=iso|vztmpl|import, file part "filename", checksum, checksum-algorithm - Datastore.AllocateTemplate`
- `POST /nodes/{node}/storage/{storage}/download-url - url, content, filename, checksum, checksum-algorithm md5|sha1|sha224|sha256|sha384|sha512, compression, verify-certificates - Datastore.AllocateTemplate + (Sys.Audit and Sys.Modify on /, or Sys.AccessNetwork on the node)`
- `POST /nodes/{node}/storage/{storage}/oci-registry-pull - none - n/a` (9.x)

## 9. Node network

- `GET /nodes/{node}/network - type=... (also any_bridge, any_local_bridge, include_sdn) - n/a`
- `POST /nodes/{node}/network - iface, type, bridge_ports, bridge_vlan_aware, cidr, gateway, autostart, mtu, slaves, bond_mode, vlan-id, vlan-raw-device, comments - Sys.Modify on /nodes/{node}` (staged only)
- `GET /nodes/{node}/network/{iface} - none - n/a`
- `PUT /nodes/{node}/network/{iface} - none - Sys.Modify on /nodes/{node}` (staged only)
- `DELETE /nodes/{node}/network/{iface} - none - Sys.Modify on /nodes/{node}` (staged; gated as DELETE)
- `PUT /nodes/{node}/network - regenerate-frr (UPID) - Sys.Modify on /nodes/{node}` (APPLY staged changes; gated)
- `DELETE /nodes/{node}/network - none - Sys.Modify on /nodes/{node}` (revert staged changes; gated as DELETE)

## 10. SDN

- `GET /cluster/sdn/zones - none - n/a`
- `POST /cluster/sdn/zones - zone, type simple|vlan|qinq|vxlan|evpn|faucet - n/a`
- `GET /cluster/sdn/zones/{zone} - none - n/a`
- `PUT /cluster/sdn/zones/{zone} - none - n/a`
- `DELETE /cluster/sdn/zones/{zone} - none - n/a` (gated)
- `GET /cluster/sdn/vnets - none - n/a`
- `POST /cluster/sdn/vnets - vnet, zone, tag, alias, vlanaware, isolate-ports - n/a`
- `GET /cluster/sdn/vnets/{vnet} - none - n/a`
- `PUT /cluster/sdn/vnets/{vnet} - none - n/a`
- `DELETE /cluster/sdn/vnets/{vnet} - none - n/a` (gated)
- `GET /cluster/sdn/vnets/{vnet}/subnets - none - n/a`
- `POST /cluster/sdn/vnets/{vnet}/subnets - subnet (CIDR), type=subnet, gateway, snat, dhcp-range, dhcp-dns-server, dnszoneprefix - n/a`
- `GET /cluster/sdn/vnets/{vnet}/subnets/{id} - none - n/a` (id format `<zone>-<ip>-<mask>`)
- `PUT /cluster/sdn/vnets/{vnet}/subnets/{id} - none - n/a`
- `DELETE /cluster/sdn/vnets/{vnet}/subnets/{id} - none - n/a` (gated)
- `PUT /cluster/sdn - lock-token, release-lock - SDN.Allocate on /sdn` (APPLY; gated)
- Paths `/cluster/sdn/rollback` (gated), `/cluster/sdn/lock` and `/cluster/sdn/dry-run` exist; their HTTP methods are UNVERIFIED, check `pvesh usage /cluster/sdn/rollback -v`.
- Other subpaths: `controllers`, `ipams`, `dns`, `fabrics` (new in 9), `prefix-lists`, `route-maps`.

## 11. Firewall

Prefixes: `/cluster/firewall` (options, rules, groups, ipset, aliases, macros, refs);
`/nodes/{node}/firewall` (options, rules, log); `/nodes/{node}/qemu/{vmid}/firewall` and
`/nodes/{node}/lxc/{vmid}/firewall` (options, rules, aliases, ipset, log, refs).

- `GET <prefix>/rules - none - n/a`
- `POST <prefix>/rules - type in|out|forward|group, action ACCEPT|DROP|REJECT|<group>, enable, source, dest, proto, dport, sport, iface, macro, icmp-type, pos, log, comment, digest - n/a`
- `GET <prefix>/rules/{pos} - none - n/a`
- `PUT <prefix>/rules/{pos} - none - n/a`
- `DELETE <prefix>/rules/{pos} - none - n/a` (gated)

## 12. HA

- `GET /cluster/ha/resources - none - n/a`
- `POST /cluster/ha/resources - sid (vm:100 | ct:101), state started|stopped|enabled|disabled|ignored, max_restart, max_relocate, failback, comment - n/a`
- `GET /cluster/ha/resources/{sid} - none - n/a`
- `PUT /cluster/ha/resources/{sid} - none - n/a` (gated when setting `state=stopped|disabled`)
- `DELETE /cluster/ha/resources/{sid} - none - n/a` (gated)
- `POST /cluster/ha/resources/{sid}/migrate - node (UNVERIFIED param name) - n/a` (gated)
- `GET /cluster/ha/rules - none - n/a`
- `POST /cluster/ha/rules - rule id and type (param names UNVERIFIED); node-affinity: resources, nodes, affinity positive|negative, strict; resource-affinity: resources, affinity - n/a`
- `GET /cluster/ha/rules/{rule} - none - n/a`
- `PUT /cluster/ha/rules/{rule} - none - n/a`
- `DELETE /cluster/ha/rules/{rule} - none - n/a` (gated)
- `/cluster/ha/groups` is deprecated in 9; the endpoints are refused once groups were
  migrated to node-affinity rules.
- `GET /cluster/ha/status/current - none - n/a`
- `GET /cluster/ha/status/manager_status - none - n/a`
- `POST /cluster/ha/status/disarm-ha - none - n/a` (gated)
- `POST /cluster/ha/status/arm-ha - none - n/a`

## 13. Access control

- `GET /access/users - none - n/a`
- `POST /access/users - none - n/a`
- `GET /access/users/{userid} - none - n/a`
- `PUT /access/users/{userid} - none - n/a`
- `DELETE /access/users/{userid} - none - n/a` (gated)
- `GET /access/users/{userid}/token - none - n/a`
- `GET /access/users/{userid}/token/{tokenid} - none - n/a`
- `POST /access/users/{userid}/token/{tokenid} - expire, privsep, comment - n/a` (returns `full-tokenid` and `value`; the value is shown once)
- `PUT /access/users/{userid}/token/{tokenid} - none - n/a`
- `DELETE /access/users/{userid}/token/{tokenid} - none - n/a` (gated)
- `GET /access/groups - none - n/a`; `GET /access/roles - - n/a`
- `GET /access/acl - none - n/a`
- `PUT /access/acl - path, roles, users, groups, tokens, propagate, delete - n/a`
- `GET /access/permissions - none - any authenticated user` (response shape UNVERIFIED)
- `PUT /access/password - none - n/a`

## 14. Cloud-init keys

Config keys on `PUT|POST .../qemu/{vmid}/config`: `citype configdrive2|nocloud|opennebula`,
`ciuser`, `cipassword`, `ciupgrade` (default 1), `cicustom` (`user=<volid>,network=,meta=,vendor=`),
`searchdomain`, `nameserver`, `sshkeys` (percent-encoded), `ipconfig0..31`
(`ip=<CIDR>|dhcp,gw=<ip>,ip6=<CIDR>|dhcp|auto,gw6=<ip>`; `gw` requires `ip`; default IPv4 dhcp).
Drive: `ide2=<storage>:cloudinit`. See `cloud-init.md`.

## 15. PVE 9.x additions

- Bulk guest actions under `/cluster/bulk-action/guest/` (start, shutdown, suspend, migrate; section 4).
- HA rules under `/cluster/ha/rules` (node-affinity, resource-affinity); `/cluster/ha/groups` deprecated.
- Resource listing item type `network` on `/cluster/resources`; SDN `fabrics` subpath.
- OCI image pull per storage (`oci-registry-pull`; section 8).
- `ha-managed` parameter on guest create.
- API tokens allowed for `termproxy` and `vncwebsocket`.
- `/monitor` requires `Sys.Audit`/`Sys.Modify` (`VM.Monitor` removed); `VM.Replicate` added.
- Storage: `maxfiles` dropped in favour of `prune-backups`; GlusterFS dropped;
  `external-snapshots` renamed `snapshot-as-volume-chain`.
