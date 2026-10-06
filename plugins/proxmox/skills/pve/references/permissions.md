# Permissions, roles and API tokens

Condensed from Proxmox 9.x sources (pve-access-control, pveum.adoc, API permission checks);
items marked UNVERIFIED were not confirmed.

## Contents

1. Model in one paragraph
2. Built-in roles
3. Privilege tiers
4. Privilege per operation
5. ACL paths
6. Recipes
7. Reading a 403

## Model in one paragraph

A token (`USER@REALM!TOKENID`) authenticates as its user. With `privsep=1` (the default)
the token's effective permissions are the intersection of the user's and the token's own
ACLs, so a fresh token has no rights until you add ACLs for it. With `privsep=0` the token
equals the user. ACLs bind a role (a set of privileges) to a path; `propagate` (default on)
pushes it down the tree; `NoAccess` wins over everything else.

## Built-in roles

Administrator, NoAccess, PVEAdmin, PVEAuditor (read-only), PVEDatastoreAdmin,
PVEDatastoreUser, PVEMappingAdmin, PVEMappingUser, PVEPoolAdmin, PVEPoolUser, PVESDNAdmin,
PVESDNUser, PVESysAdmin, PVETemplateUser (VM.Clone + VM.Audit), PVEUserAdmin, PVEVMAdmin,
PVEVMUser. List the exact privilege sets with `pve-api.sh GET /access/roles`. A PVEAuditor
token on `/` reports exactly seven privileges: Datastore.Audit, Mapping.Audit, Pool.Audit,
SDN.Audit, Sys.Audit, VM.Audit, VM.GuestAgent.Audit (confirmed on a PVE 9.2 cluster).

## Privilege tiers

| Family | Audit | User | Admin | Root-only |
|---|---|---|---|---|
| VM | VM.Audit, VM.GuestAgent.Audit | VM.Config.CDROM, VM.Config.Cloudinit, VM.Console, VM.Backup, VM.GuestAgent.FileRead, VM.GuestAgent.FileWrite, VM.GuestAgent.FileSystemMgmt, VM.PowerMgmt | VM.Config.Disk, VM.Config.CPU, VM.Config.Memory, VM.Config.Network, VM.Config.HWType, VM.Config.Options, VM.Allocate, VM.Clone, VM.GuestAgent.Unrestricted, VM.Migrate, VM.Replicate, VM.Snapshot, VM.Snapshot.Rollback | |
| Sys | Sys.Audit | | Sys.Console, Sys.Syslog | Sys.PowerMgmt, Sys.Modify, Sys.Incoming, Sys.AccessNetwork |
| Datastore | Datastore.Audit | Datastore.AllocateSpace | Datastore.Allocate, Datastore.AllocateTemplate | |
| SDN | SDN.Audit | SDN.Use | SDN.Allocate | |
| Pool | Pool.Audit | | Pool.Allocate | |
| Mapping | Mapping.Audit | Mapping.Use | Mapping.Modify | |

`VM.Monitor` was removed in PVE 9 (the monitor endpoint needs Sys.Audit/Sys.Modify);
`VM.Replicate` was added.

Root-only tier: Sys.PowerMgmt, Sys.Modify, Sys.Incoming and Sys.AccessNetwork are classed
root-only in the source (AccessControl.pm). That label is a tier name, not a restriction to
`root@pam`: a non-root principal can hold them (confirmed on a PVE 9.2 cluster, where an
LDAP/Authentik-backed group holds the Administrator role on `/` with propagate=1). These
privileges are in the Administrator role, not in PVEVMAdmin or PVEAuditor, so node reboot,
network apply, apt refresh and backup-job edits need a user, group or token holding
Administrator on `/` or on `/nodes/{node}`; the SSH tier is the alternative. Test with
`/proxmox:doctor` before planning such actions.

## Privilege per operation

| Operation | Privilege (path) |
|---|---|
| read guest status/config | VM.Audit on /vms/{vmid} |
| start/stop/shutdown/reboot/reset/suspend/resume | VM.PowerMgmt on /vms/{vmid} |
| snapshot create/delete | VM.Snapshot on /vms/{vmid} |
| snapshot rollback | VM.Snapshot or VM.Snapshot.Rollback on /vms/{vmid} |
| clone | VM.Clone on the source + VM.Allocate on /vms/{newid} or the pool |
| create guest | VM.Allocate + Datastore.AllocateSpace on the storage + SDN.Use on the bridge |
| restore over an existing guest | VM.Backup |
| destroy, convert to template | VM.Allocate on /vms/{vmid} |
| migrate | VM.Migrate on /vms/{vmid} |
| resize disk | VM.Config.Disk on /vms/{vmid} |
| other config keys | VM.Config.* by key (CPU, Memory, Network, HWType, Options, CDROM, Cloudinit) |
| guest agent exec | VM.GuestAgent.Unrestricted |
| node reboot/shutdown | Sys.PowerMgmt on /nodes/{node} |
| network edit/apply, apt refresh | Sys.Modify on /nodes/{node} |
| apt versions | Sys.Audit |
| cluster status | Sys.Audit on / |
| backup jobs (/cluster/backup) | Sys.Modify on / |
| SDN apply (PUT /cluster/sdn) | SDN.Allocate on /sdn |
| upload ISO/template | Datastore.AllocateTemplate on /storage/{storage} |
| download-url | Datastore.AllocateTemplate + (Sys.Audit and Sys.Modify on /, or Sys.AccessNetwork on the node) |
| /cluster/resources, /cluster/nextid | any authenticated user (results filtered) |

## ACL paths

`/`, `/nodes/{node}`, `/vms`, `/vms/{vmid}`, `/storage/{storeid}`, `/pool/{pool}`,
`/access/groups`, `/access/realm/{realm}`, `/sdn/zones/<zone>/<vnet>`. Grant on the
broadest path that still fits: `/vms` for "all guests", `/pool/X` for a pool, `/vms/{vmid}`
for one guest.

## Recipes

Creating a token prints its secret once, so the user runs every `token add` below
(or uses Datacenter > Permissions > API Tokens in the web UI) in their own shell, not
through Claude: output of a command Claude runs, over SSH or the API, lands in the
transcript. Claude can prepare the commands and run the ACL grants afterwards.

Read-only token (inventory, status, task logs, doctor). With `-privsep 0` the token
inherits the user's ACL, so one grant is enough:

```
pveum user add claude@pve -comment "Claude Code"
pveum user token add claude@pve ro -privsep 0 -comment "Claude read-only"
pveum acl modify / -user claude@pve -role PVEAuditor -propagate 1
```

Variant: privilege-separated token (`-privsep 1`, the default). Effective rights are the
intersection of user and token, so both the user and the token need the ACL:

```
pveum user token add claude@pve ro -privsep 1 -comment "Claude read-only"
pveum acl modify / -user claude@pve -role PVEAuditor -propagate 1
pveum acl modify / -token 'claude@pve!ro' -role PVEAuditor -propagate 1
```

Operator token (lifecycle, snapshots, clones, backups, staged SDN objects):

```
pveum user token add claude@pve ops -privsep 1 -comment "Claude operator"
pveum acl modify /vms -token 'claude@pve!ops' -role PVEVMAdmin -propagate 1
pveum acl modify /storage/<id> -token 'claude@pve!ops' -role PVEDatastoreUser -propagate 1
pveum acl modify /sdn -token 'claude@pve!ops' -role PVESDNUser -propagate 1
```

Grant the same roles to the user (`-user claude@pve`) or the intersection with a
rights-less user is empty. Replace `/vms` with `/pool/<pool>` to scope to a pool.

ACL via the API, once the user has created the token themselves (`POST
/access/users/<user>/token/<name>` returns the secret in `.data.value`, and `pve-api.sh`
prints `.data` as is, so Claude never calls it):

```
pve-api.sh PUT /access/acl path=/vms roles=PVEVMAdmin tokens='claude@pve!ops' propagate=1
```

Check what a token can do: `pve-api.sh GET /access/permissions`. The response is an object
keyed by ACL path (`/`, `/storage`, `/sdn`, `/vms`, `/access`, ...), each value an object
`{privilege-name: 1}`, not an array (confirmed on a PVE 9.2 cluster); `/proxmox:doctor`
summarises it. Set `expire` (epoch) on tokens that should not live forever; an expired token
answers 401 with `access expired`.

## Reading a 403

`pve-api.sh` exits 3 and prints the message, which names the ACL path and the privilege
exactly (confirmed on a PVE 9.2 cluster):

```
pve-api: HTTP 403 POST /nodes/<node>/lxc/109/status/stop: Permission check failed (/vms/109, VM.PowerMgmt)
```

Map it with the table above and the tiers: a `VM.*` privilege means the token needs
PVEVMAdmin (or a custom role) on `/vms/{vmid}` or above; `Datastore.*` means a storage role
on `/storage/{id}`; `Sys.*` means a node-level privilege that only the Administrator role
(on `/` or `/nodes/{node}`) carries, which a scoped token usually lacks.
Re-run `/proxmox:doctor` after changing ACLs. Do not retry with `root@pam` credentials on
your own initiative; ask the user.
