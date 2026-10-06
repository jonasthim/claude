# Sharing: SMB, NFS, iSCSI, ACLs

Check shapes with `tn.py methods sharing.smb.create --schema`: SMB share options moved around
between 25.04 and 25.10, so the live schema decides.

## SMB

```
tn.py query sharing.smb --select id,name,path,enabled,ro,guestok,purpose,comment,locked
tn.py call smb.config                                # workgroup, netbios name, AD/LDAP state
tn.py call smb.status SESSIONS                       # who is connected (SESSIONS | SHARES | LOCKS)
tn.py call sharing.smb.presets                       # purpose presets and what they set
tn.py call service.restart cifs                      # after share or ACL changes
```

Create (not gated). 25.04 shape:

```
tn.py call sharing.smb.create '{"path": "/mnt/tank/photos", "name": "photos",
  "purpose": "DEFAULT_SHARE", "comment": "family photos", "enabled": true,
  "ro": false, "guestok": false, "browsable": true, "recyclebin": false}'
```

25.10 shape: `purpose` is one of `DEFAULT_SHARE`, `TIMEMACHINE_SHARE`, `MULTIPROTOCOL_SHARE`,
`PRIVATE_DATASETS_SHARE`, `EXTERNAL_SHARE`, `TIME_LOCKED_SHARE`, `VEEAM_REPOSITORY_SHARE`,
`LEGACY_SHARE`, and per-purpose settings sit under `options` (e.g. `{"options": {"aapl_name_mangling": false}}`).
Legacy flags such as `ro`, `guestok`, `hostsallow` still exist at top level for `LEGACY_SHARE`.
Run `methods sharing.smb.create --schema` before writing the payload.

Update: `sharing.smb.update <id> {ro: true}`. Delete is gated: `sharing.smb.delete <id>`.

Gotchas:
- The dataset should be created with `share_type: "SMB"` (NFSv4 ACLs, case-insensitive). A
  share on a `GENERIC` dataset works but permissions behave like POSIX.
- `locked: true` in query output means the dataset is encrypted and locked; the share is
  down until unlocked.
- Users need an SMB password: `user.query --filter smb=true`. `user.create`/`user.update`
  with `"smb": true` are gated (user namespace); explain and ask.
- Share-level ACL (who may connect) is separate from filesystem ACL:
  `sharing.smb.getacl {share_name}` / `sharing.smb.setacl {share_name, share_acl:[...]}` (gated).
- `smb.update` (global config, e.g. workgroup) is gated and restarts Samba.

## NFS

```
tn.py query sharing.nfs --select id,path,comment,networks,hosts,ro,maproot_user,mapall_user,enabled
tn.py call nfs.config                                # protocols (NFSv3/NFSv4), allow_nonroot, threads
tn.py call service.restart nfs
```

Create (not gated):

```
tn.py call sharing.nfs.create '{"path": "/mnt/tank/media", "comment": "media for kodi",
  "networks": ["192.168.1.0/24"], "hosts": [], "ro": true,
  "maproot_user": null, "maproot_group": null, "mapall_user": null, "mapall_group": null,
  "security": [], "enabled": true}'
```

- `networks` are CIDR strings; `hosts` are hostnames/IPs. Empty lists mean everyone.
- `maproot_user: "root"` lets root on the client act as root; `mapall_user` squashes all
  clients to one account (common for media boxes).
- NFSv4 with `security: ["SYS"]` is default-ish; Kerberos needs AD/LDAP setup.
- Each path can have only one NFS share in 25.x; `EEXIST` means edit the existing one.
- `nfs.update` (enable NFSv4, change threads) is gated.

## iSCSI

An iSCSI export needs five objects: portal (listen address), initiator group (who may
connect), target (the IQN clients see), extent (the zvol or file), and a target-extent
mapping (LUN). Create in that order.

```
tn.py call iscsi.global.config                       # base name, e.g. iqn.2005-10.org.freenas.ctl
tn.py query iscsi.portal                             # usually one: 0.0.0.0:3260
tn.py query iscsi.initiator
tn.py query iscsi.target --select id,name,alias,groups
tn.py query iscsi.extent --select id,name,type,disk,path,filesize,enabled,ro
tn.py query iscsi.targetextent
tn.py call service.restart iscsitarget
```

Creating a zvol-backed LUN (none of these are gated; the zvol create is in storage.md):

```
tn.py call iscsi.portal.create '{"listen": [{"ip": "0.0.0.0"}], "comment": "default"}'
tn.py call iscsi.initiator.create '{"initiators": [], "comment": "any initiator"}'
tn.py call iscsi.target.create '{"name": "vm-disk0", "alias": "proxmox disk",
  "groups": [{"portal": 1, "initiator": 1, "auth": null, "authmethod": "NONE"}]}'
tn.py call iscsi.extent.create '{"name": "vm-disk0", "type": "DISK", "disk": "zvol/tank/vm/disk0",
  "blocksize": 512, "enabled": true, "ro": false}'
tn.py call iscsi.targetextent.create '{"target": 1, "extent": 1, "lunid": 0}'
```

- `disk` for a DISK extent is `zvol/<pool>/<path>` (no `/dev/`). FILE extents use `path` and `filesize`.
- Full IQN = `<basename>:<target name>`. Report it to the user.
- Changing an extent that a client has mounted can corrupt the client's filesystem; ask first
  even though `update` is not gated.
- CHAP: `iscsi.auth.create {tag, user, secret, peeruser, peersecret}` then reference `tag` in
  the target group's `auth` with `authmethod: "CHAP"`.
- `iscsi.global.update` (base name, ALUA, ISNS) is gated.

## Filesystem ACLs and permissions

```
tn.py call filesystem.stat /mnt/tank/photos
tn.py call filesystem.getacl /mnt/tank/photos true   # second arg: simplified
tn.py call filesystem.listdir /mnt/tank/photos '[]' '{"limit": 50}'
tn.py call filesystem.acltemplate.by_path '{"path": "/mnt/tank/photos", "format-options": {"canonicalize": true}}'
```

Changing ACLs or ownership is gated and runs as a job:

```
tn.py call pool.dataset.permission tank/photos '{"user": "alice", "group": "family",
  "mode": null, "acl": [], "options": {"stripacl": false, "recursive": true, "traverse": false}}' --confirm --job
tn.py call filesystem.setacl '{"path": "/mnt/tank/photos", "dacl": [...], "uid": -1, "gid": -1,
  "options": {"recursive": true, "traverse": false, "stripacl": false}}' --confirm --job
tn.py call filesystem.chown '{"path": "/mnt/tank/photos", "uid": 1001, "gid": 1001,
  "options": {"recursive": true}}' --confirm --job
```

- Take a snapshot before any recursive ACL change: a wrong ACL on 500k files is slow to undo.
- NFSv4 ACL entries: `{"tag": "USER"|"GROUP"|"owner@"|"group@"|"everyone@", "id": <uid|gid|-1>,
  "type": "ALLOW"|"DENY", "perms": {"BASIC": "FULL_CONTROL"|"MODIFY"|"READ"|"TRAVERSE"},
  "flags": {"BASIC": "INHERIT"|"NOINHERIT"}}`. Use templates from `acltemplate.by_path` as a base.
- POSIX ACL datasets (`acltype: POSIX`) take `{"tag": "USER_OBJ"|"GROUP_OBJ"|"OTHER"|"MASK"|"USER"|"GROUP",
  "id", "perms": {"READ","WRITE","EXECUTE"}, "default": bool}` entries instead.
- `uid`/`gid` of `-1` or `null` means "leave as is".
- After ACL changes on an SMB share, clients may need to reconnect; a `service.restart cifs`
  is not required.
