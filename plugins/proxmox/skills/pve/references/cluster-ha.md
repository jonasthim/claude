# Cluster, nodes and HA

Condensed from Proxmox 9.x sources (pve-cluster, pve-manager node API, pve-ha-manager,
pve-docs); items marked UNVERIFIED were not confirmed.

## Cluster and node status

```
pve-api.sh GET /cluster/status            # nodes and quorum (item shape UNVERIFIED)
pve-api.sh GET /nodes                     # node list with status
pve-api.sh GET /nodes/N/status
pve-api.sh GET /cluster/resources type=node
pve-api.sh GET /cluster/log max=50
pve-api.sh GET /cluster/tasks
pve-ssh.sh -n N pvecm status              # corosync view
pve-ssh.sh -n N pvecm nodes
```

Before any node-level action, list what runs there: `GET /cluster/resources type=vm` and
filter on `node`, and `GET /cluster/ha/resources` for HA-managed guests, because HA will
act on them when the node goes away.

## Node power and bulk guest actions (all gated)

- `POST /nodes/N/status command=reboot|shutdown` (Sys.PowerMgmt on `/nodes/N`).
- `POST /nodes/N/startall` (free), `POST /nodes/N/stopall`, `POST /nodes/N/suspendall`,
  `POST /nodes/N/migrateall target=N2 (param name UNVERIFIED)` (gated). CLI: `pvenode startall [--vms] [--force]`,
  `pvenode stopall`, `pvenode migrateall <target> [--vms] [--with-local-disks] [--max-workers]`.
- 9.x bulk actions: `POST /cluster/bulk-action/guest/start|shutdown|suspend|migrate`
  with `vms`, `timeout`, `max-workers`; start is free, the rest gated.
- Wake a node: `pvenode wakeonlan <node>` over SSH.

Node maintenance mode has no REST endpoint; over SSH:
`ha-manager crm-command node-maintenance enable <node>` before the work and
`... disable <node>` afterwards. Both gated.

## Updates

```
pve-api.sh POST /nodes/N/apt/update | pve-task.sh -     # refresh package lists (free)
pve-api.sh GET /nodes/N/apt/update                      # pending updates
pve-api.sh GET /nodes/N/apt/versions
pve-api.sh GET /nodes/N/apt/repositories
pve-api.sh GET /nodes/N/apt/changelog
```

There is no upgrade endpoint and no `pvenode updates` command. Installing updates is
`apt dist-upgrade` over SSH, gated; a kernel update then needs a node reboot, also gated.
Order for a cluster: one node at a time; migrate or shut down guests, maintenance mode,
upgrade, reboot, verify `GET /nodes/N/status` and `pvecm status`, maintenance off, next node.

## Cluster membership (gated; SSH only)

- `pvecm create <name>`; `pvecm add <hostname> [--link0 IP] [--link1 IP] [--use_ssh] [--fingerprint] [--force]`;
  `pvecm addnode`; `pvecm delnode <node>`; `pvecm expected <n>`;
  `pvecm qdevice setup <addr> | remove`; `pvecm updatecerts`; `pvecm keygen`; `pvecm apiver`.
- `delnode` warning: power the node off first; a removed node cannot rejoin without a
  reinstall. Never run it on a node that is still up.
- `pvecm expected <n>` changes the expected vote count; use it only in a documented
  recovery and set it back afterwards.

## HA resources

```
pve-api.sh GET /cluster/ha/resources
pve-api.sh GET /cluster/ha/resources/vm:100
pve-api.sh GET /cluster/ha/status/current
pve-api.sh GET /cluster/ha/status/manager_status
```

- Add (free): `POST /cluster/ha/resources sid=vm:100 state=started` with optional
  `max_restart`, `max_relocate`, `failback`, `comment`. `sid` is `vm:<vmid>` or `ct:<vmid>`.
  States: `started`, `stopped`, `enabled`, `disabled`, `ignored`.
- Change (gated when `state=stopped|disabled`): `PUT /cluster/ha/resources/{sid}`;
  CLI `ha-manager set <sid> --state started|stopped|disabled|ignored`.
- Remove (gated): `DELETE /cluster/ha/resources/{sid}`; `ha-manager remove <sid>`.
- Migrate/relocate through HA (gated): `POST /cluster/ha/resources/{sid}/migrate`;
  `ha-manager migrate|relocate <sid> <node>`; `ha-manager crm-command migrate|relocate <sid> <node>`;
  `ha-manager crm-command stop <sid> <timeout>`.
- Disarm/arm (gated/free): `POST /cluster/ha/status/disarm-ha`, `POST /cluster/ha/status/arm-ha`;
  CLI `ha-manager crm-command disarm-ha freeze|ignore`, `ha-manager crm-command arm-ha` (9.2).
- Guests can be created HA-managed directly with `ha-managed` on `POST /nodes/N/qemu|lxc`.

For an HA-managed guest change the resource state (gated) instead of only stopping it;
whether HA restarts a guest after a plain `status/stop` is UNVERIFIED. Destroying an HA
guest needs `purge=1`.

## HA rules (PVE 9)

HA groups are deprecated; existing groups were auto-migrated to node-affinity rules and
the group endpoints are refused once migrated. Use `/cluster/ha/rules`.

- `GET /cluster/ha/rules`; `GET|PUT|DELETE /cluster/ha/rules/{rule}`.
- `POST /cluster/ha/rules` with the rule id and type (parameter names UNVERIFIED; check
  `pvesh usage /cluster/ha/rules -v --command create`), then:
  node-affinity: `resources`, `nodes`, `affinity positive|negative`, `strict`;
  resource-affinity: `resources`, `affinity positive|negative`.

CLI forms (verbatim from the docs):

```
ha-manager rules add node-affinity ha-rule-vm100 --resources vm:100 --nodes node1
ha-manager rules add resource-affinity keep-together --affinity positive --resources vm:100,vm:200
ha-manager rules list
ha-manager rules config
```

The list separator for several `nodes` and any per-node priority syntax is UNVERIFIED;
`pvesh usage` prints the accepted format. Rule add/list are free; `ha-manager rules set|remove`
change placement and are gated: confirm first, like `ha-manager remove|set|migrate|relocate|crm-command`.

Pattern "make CT 105 highly available, prefer pve1 or pve2":

1. `GET /cluster/ha/resources` to confirm `ct:105` is not managed yet.
2. `POST /cluster/ha/resources sid=ct:105 state=started`.
3. `POST /cluster/ha/rules` creating a node-affinity rule for `ct:105` with
   `nodes` naming pve1 and pve2 (leave `strict` unset unless the user wants a hard pin).
4. `GET /cluster/ha/status/current` and `GET /cluster/ha/rules` to verify.
