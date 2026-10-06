# PVE 9 vs PVE 8: what changed

Condensed from Proxmox 9.x sources (debian/changelogs, pve-docs) and release summaries
(Roadmap, Upgrade_from_8_to_9, press releases); items marked UNVERIFIED were not confirmed.

## Releases

| Version | Date | Base | Highlights |
|---|---|---|---|
| 9.0 | (not in notes) | Debian 13 Trixie, kernel 6.14 | LVM snapshots as volume chains (`snapshot-as-volume-chain`, tech preview, qcow2); SDN fabrics (OpenFabric, OSPF); HA resource affinity rules |
| 9.1 | 2025-11 | kernel 6.17, QEMU 10.1 | LXC containers from OCI images (tech preview) |
| 9.2 | 2026-05 | kernel 7.0, QEMU 11 | Dynamic Load Balancer; WireGuard SDN fabric; HA arm/disarm (`crm-command arm-ha|disarm-ha`) |

## Breaking changes

- `VM.Monitor` privilege removed. `POST .../qemu/{vmid}/monitor` now needs `Sys.Audit`
  or `Sys.Modify`. Check custom roles that referenced `VM.Monitor`. Guest agent
  access is split into `VM.GuestAgent.Audit`, `.FileRead`, `.FileWrite`,
  `.FileSystemMgmt`, `.Unrestricted`; `VM.Replicate` was added.
- HA groups deprecated. Existing groups are auto-migrated to node-affinity rules and the
  `/cluster/ha/groups` endpoints are refused once migrated. Use `/cluster/ha/rules`
  (node-affinity, resource-affinity); `ha-manager groupadd|groupset` exist but are legacy.
- cgroup v1 is gone: containers with old systemd versions are unsupported.
- Storage: `maxfiles` dropped in favour of `prune-backups`; GlusterFS storage dropped;
  `external-snapshots` renamed `snapshot-as-volume-chain`; storage migration refused for
  guests with volume-chain snapshots.

## Additions worth using

- `POST /cluster/bulk-action/guest/{start,shutdown,suspend,migrate}` with `vms`,
  `timeout`, `max-workers`.
- `GET /cluster/resources` item type `network`; SDN `fabrics` subpath.
- `POST .../storage/{storage}/oci-registry-pull` and LXC from OCI images.
- `ha-managed` parameter on guest create (`POST /nodes/N/qemu|lxc`).
- API tokens may use `termproxy` and `vncwebsocket`.
- `ha-manager crm-command arm-ha|disarm-ha` (9.2) and `POST /cluster/ha/status/arm-ha|disarm-ha`.

## Upgrade path 8 to 9

Update to the latest 8.4 first, run `pve8to9` on every node and fix what it reports, then
move the apt sources from bookworm to trixie using deb822 `.sources` files
(`apt modernize-sources`), and upgrade one node at a time (`cluster-ha.md` has the order).
All steps run over SSH and are gated (`apt dist-upgrade`, reboot).

## Unchanged

No core CLI command was renamed in 9; `qm importdisk`, `qm resize`, `qm move-disk` remain
as aliases of `qm disk import|resize|move`. `/api2/json` paths used by this skill are the
same as in 8 except where listed above.
