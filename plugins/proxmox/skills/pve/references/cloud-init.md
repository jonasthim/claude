# Cloud-init templates and provisioning

Condensed from Proxmox 9.x sources (qm-cloud-init.adoc, qemu-server); items marked
UNVERIFIED were not confirmed.

## Contents

1. Recipe over SSH (verbatim from qm-cloud-init.adoc)
2. Same recipe over the API
3. Config keys
4. sshkeys encoding
5. Pitfalls

## Recipe over SSH (verbatim from qm-cloud-init.adoc)

```
qm create 9000 --memory 2048 --net0 virtio,bridge=vmbr0 --scsihw virtio-scsi-pci
qm set 9000 --scsi0 local-lvm:0,import-from=/path/to/bionic-server-cloudimg-amd64.img
qm set 9000 --ide2 local-lvm:cloudinit
qm set 9000 --boot order=scsi0
qm set 9000 --serial0 socket --vga serial0
qm template 9000
qm clone 9000 123 --name ubuntu2
qm set 123 --sshkey ~/.ssh/id_rsa.pub
qm set 123 --ipconfig0 ip=10.0.10.123/24,gw=10.0.10.1
qm set 9000 --cicustom "user=local:snippets/userconfig.yaml"
qm cloudinit dump 9000 user
```

`qm template` is gated (irreversible). On the CLI `--sshkeys` takes a file path and qm
encodes it; `--cipassword` prompts interactively.

## Same recipe over the API

1. Get the image onto a storage the token can read. API tokens cannot use absolute paths
   in `import-from` (root@pam only), so download into a storage with content type
   `import` (or `images`) first:

   ```
   pve-api.sh POST /nodes/N/storage/S/download-url url=https://.../noble-server-cloudimg-amd64.img content=import filename=noble-server-cloudimg-amd64.img checksum-algorithm=sha256 checksum=...
   ```

   (returns a UPID: UNVERIFIED; if the response is a bare UPID string, pipe it to
   `pve-task.sh -`). Needs Datastore.AllocateTemplate plus Sys.Audit/Sys.Modify on `/` or Sys.AccessNetwork
   on the node. Then find the volid: `pve-api.sh GET /nodes/N/storage/S/content content=import`.

2. Create the VM, then import the disk with a config write (the documented path is
   `qm set --scsi0 STORAGE:0,import-from=...`; `POST .../config` is its async API form):

   ```
   pve-api.sh GET /cluster/nextid
   pve-api.sh POST /nodes/N/qemu vmid=9000 name=noble-template memory=2048 cores=2 net0=virtio,bridge=vmbr0 scsihw=virtio-scsi-pci | pve-task.sh -
   pve-api.sh GET /nodes/N/qemu/9000/config            # take digest
   pve-api.sh POST /nodes/N/qemu/9000/config scsi0=local-lvm:0,import-from=IMPORT_VOLID ide2=local-lvm:cloudinit boot=order=scsi0 serial0=socket vga=serial0 digest=D | pve-task.sh -
   ```

   `IMPORT_VOLID` is the volid from the content listing in step 1. Passing
   `scsi0=...,import-from=...` directly on `POST /nodes/N/qemu` (import inside the create
   task) is UNVERIFIED; prefer the two-step form above. `PUT .../config` also accepts the
   key but is synchronous, so it blocks until the import finishes.
   `scsi0=<storage>:0,import-from=<volid>` imports; `ide2=<storage>:cloudinit` is the
   documented cloud-init drive slot (`scsi1` as the slot is UNVERIFIED). Optional:
   `efidisk0=<storage>:1,efitype=4m,pre-enrolled-keys=1` (needs `efitype` and
   `pre-enrolled-keys`; exact values UNVERIFIED) and `tpmstate0=<storage>:1,version=v2.0`
   (needs `version`; exact value UNVERIFIED).

3. Convert to a template (gated; PLAN first):

   ```
   pve-api.sh POST /nodes/N/qemu/9000/template | pve-task.sh -
   ```

4. Clone per instance. Templates clone linked by default; pass `full=1` for an
   independent copy (`storage=` and `format=` apply to full clones only):

   ```
   pve-api.sh POST /nodes/N/qemu/9000/clone newid=123 name=web01 full=1 storage=local-lvm | pve-task.sh -
   ```

5. Configure the instance (free; pass `digest` from `GET .../config`):

   ```
   pve-api.sh PUT /nodes/N/qemu/123/config ciuser=admin sshkeys=ssh-ed25519%20AAAA...%20user%40host ipconfig0=ip=10.0.10.123/24,gw=10.0.10.1 nameserver=10.0.10.1 searchdomain=lab.local digest=D
   pve-api.sh POST /nodes/N/qemu/123/status/start
   ```

6. Inspect the generated data: `pve-api.sh GET /nodes/N/qemu/123/cloudinit/dump type=user`
   (also `network`, `meta`). `GET .../cloudinit` lists pending cloud-init changes;
   `PUT .../cloudinit` regenerates the drive without a config change.

## Config keys

| Key | Value |
|---|---|
| `citype` | `configdrive2`, `nocloud`, `opennebula` |
| `ciuser` | login user name |
| `cipassword` | password (prefer `sshkeys`) |
| `ciupgrade` | default 1: run a package upgrade on first boot |
| `cicustom` | `user=<volid>,network=<volid>,meta=<volid>,vendor=<volid>`, e.g. `user=local:snippets/userconfig.yaml` |
| `sshkeys` | percent-encoded public keys, see below |
| `ipconfig0` .. `ipconfig31` | `ip=<CIDR>|dhcp,gw=<ip>,ip6=<CIDR>|dhcp|auto,gw6=<ip>`; `gw` requires `ip`; default IPv4 dhcp |
| `nameserver` | DNS server(s) |
| `searchdomain` | DNS search domain |

## sshkeys encoding

The validator accepts only `^[-%a-zA-Z0-9_.!~*'()]*$` and the server `uri_unescape`s the
value, so every other character must be percent-encoded. `+` is not a space.

| Character | Encoded |
|---|---|
| space | `%20` |
| `+` | `%2B` |
| `/` | `%2F` |
| `=` | `%3D` |
| `@` | `%40` |
| newline (between keys) | `%0A` |

Build it with jq so nothing is missed:

```
KEYS=$(jq -rn --rawfile k ~/.ssh/id_ed25519.pub '$k | rtrimstr("\n") | @uri')
pve-api.sh PUT /nodes/N/qemu/123/config sshkeys="$KEYS" digest=D
```

`pve-api.sh` form-encodes the body, so the value is double-encoded on the wire; that is
expected. LXC containers are different: `ssh-public-keys` on `POST /nodes/N/lxc` is plain
text, never encoded.

## Pitfalls

- `import-from` with an absolute path works only as root@pam; tokens use a volid.
- Source volume content type must be `images` or `import`.
- Set `ipconfig0` and `sshkeys` on each clone (the recipe sets them on 123, not on
  9000); values left on the template apply to every clone.
- `GET .../cloudinit` lists pending cloud-init changes; `cloudinit/dump` shows the
  generated user/network/meta data; `PUT .../cloudinit` regenerates the drive.
