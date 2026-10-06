# Storage

Condensed from Proxmox 9.x sources (pve-storage, pvesm/pveam docs); items marked
UNVERIFIED were not confirmed.

## Discover

```
pve-api.sh GET /storage                                   # cluster storage definitions
pve-api.sh GET /nodes/N/storage                           # per-node view, usage
pve-api.sh GET /nodes/N/storage content=images enabled=1  # filters: storage, content, enabled, target, format
pve-api.sh GET /nodes/N/storage/S/status
pve-api.sh GET /nodes/N/storage/S/content content=backup vmid=100
pve-api.sh GET /cluster/resources type=storage            # usage across the cluster (disk, maxdisk)
```

Content types seen in `content=` filters: `images` (guest disks), `iso`, `vztmpl`
(CT templates), `backup`, `snippets`, `import`. A volume id looks like
`local:vztmpl/debian-10.0-standard_10.0-1_amd64.tar.gz` (`<storage>:<content>/<name>`).

## Define and edit storage (free)

`POST /storage`, `PUT /storage/{storage}`, `DELETE /storage/{storage}` (gated). The
parameter set depends on the type; print it with `pvesh usage /storage -v --command create`
over SSH. CLI: `pvesm add <type> <storage>`, `pvesm set`, `pvesm remove` (gated),
`pvesm scan nfs|cifs|iscsi|lvm|lvmthin|pbs|zfs`.

## Get ISOs and templates onto storage

Download from a URL (free; needs Datastore.AllocateTemplate plus Sys.Audit and Sys.Modify
on `/` or Sys.AccessNetwork on the node):

```
pve-api.sh POST /nodes/N/storage/S/download-url url=https://... content=iso filename=debian.iso checksum-algorithm=sha256 checksum=... verify-certificates=1
```

(returns a UPID: UNVERIFIED; if the response is a bare UPID string, pipe it to
`pve-task.sh -`). Params: `url`, `content` (`iso|vztmpl|import`), `filename`, `checksum`,
`checksum-algorithm md5|sha1|sha224|sha256|sha384|sha512`, `compression`,
`verify-certificates`. There is no `pvesm download-url` CLI command.

Upload a local file (multipart; `pve-api.sh` does not do multipart, use curl):

```
curl -sS --cacert "$PVE_CA_CERT" -H "Authorization: PVEAPIToken=${PVE_TOKEN_ID}=${PVE_TOKEN_SECRET}" \
  -F content=iso -F filename=@/path/debian.iso -F checksum-algorithm=sha256 -F checksum=... \
  "https://HOST:8006/api2/json/nodes/N/storage/S/upload"
```

Needs Datastore.AllocateTemplate. HTTP 506 means a bad upload content type.

CT templates: `pveam update`, `pveam available --section system`,
`pveam download <storage> <template>`, `pveam list <storage>`, `pveam remove <path>` (gated).
Container images from OCI registries (9.x): `POST /nodes/N/storage/S/oci-registry-pull`
(params not in the notes; `pvesh usage` to see them).

## Volumes

- Allocate: `POST /nodes/N/storage/S/content` (`pvesm alloc <storage> <vmid> <filename> <size> [--format]`).
- Inspect: `GET /nodes/N/storage/S/content/{volume}`; `pvesm path <volume>`;
  `pvesm extractconfig <volume>` (config inside a backup archive).
- Delete: `DELETE /nodes/N/storage/S/content/{volume}` or `pvesm free <volume>`; gated.
- Prune backups: `POST /nodes/N/storage/S/prunebackups` or `pvesm prune-backups`; gated.

## Guest disks

- Resize (free): `PUT /nodes/N/qemu/V/resize disk=scsi0 size=+10G digest=D`
  (also `/lxc`); CLI `qm disk resize V scsi0 +10G`, `pct resize V rootfs +4G`.
- Move between storages (gated): `POST /nodes/N/qemu/V/move_disk`, `POST /nodes/N/lxc/V/move_volume`;
  CLI `qm disk move V scsi0 <storage>`, `pct move-volume V mp0 <storage>`.
- Unlink or remove a disk from a config (gated): `PUT .../config delete=scsi1`, `qm disk unlink`.
- Import an image as a disk: `scsi0=S:0,import-from=<volid>` on `qm set` or
  `PUT|POST .../config` (documented); the same key on `POST /nodes/N/qemu` (create) is
  UNVERIFIED. Absolute paths are root@pam only. See `cloud-init.md`.

## PVE 9 notes

- `maxfiles` is gone; use `prune-backups` (keep-last etc.) on storages and jobs.
- GlusterFS storage was dropped.
- `external-snapshots` was renamed `snapshot-as-volume-chain` (LVM snapshots as qcow2
  volume chains, tech preview). Storage migration is refused for guests that have
  volume-chain snapshots; remove the snapshots first (gated) or keep the guest in place.
