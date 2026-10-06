# Cluster, nodes and HA

Condensed from Proxmox 9.x sources (pve-cluster, pve-manager node API, pve-ha-manager,
pve-docs); items marked UNVERIFIED were not confirmed.

## Contents

1. Cluster and node status
2. Node power and bulk guest actions (all gated)
3. Updates
4. Cluster membership (gated; SSH only)
5. HA resources
6. HA rules (PVE 9)
7. Storage replication

## Cluster and node status

```
pve-api.sh GET /cluster/status            # nodes and quorum; mixed array, branch on type
pve-api.sh GET /nodes                     # node list with status
pve-api.sh GET /nodes/N/status
pve-api.sh GET /cluster/resources type=node
pve-api.sh GET /cluster/log max=50
pve-api.sh GET /cluster/tasks
pve-ssh.sh -n N pvecm status              # corosync view
pve-ssh.sh -n N pvecm nodes
```

`/cluster/status` items (confirmed on a PVE 9.2 cluster): exactly one `type=cluster` item
with `id, name, nodes, quorate, type, version`, and one `type=node` item per node with
`id, ip, level, local, name, nodeid, online, type`. Branch on `type` when iterating; quorum
is `quorate` on the cluster item, membership is `online` on the node items.

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

## Storage replication

A replication job (`pvesr`) copies a guest's volumes on local ZFS storage to another node on
a schedule. An HA-managed guest on local storage can only be recovered on a node that holds a
replica of it, so "which HA guests are replicated, and to where" is a question worth getting
right.

Two routes answer two different questions, and neither answers the other's (both confirmed on
a 3-node PVE 9 cluster):

| Question | Read | Scope | Fields |
|---|---|---|---|
| Coverage: which guests are replicated, from where, to where | `pve-api.sh GET /cluster/replication` | Whole cluster | `id`, `guest`, `jobnum`, `source`, `target`, `schedule`, `type` |
| Health: is each job syncing | `pve-api.sh GET /nodes/N/replication`, once per node from `GET /nodes` | **Only the jobs whose source is N** | The above plus `fail_count`, `last_sync`, `last_try`, `next_sync`, `duration`, `vmtype` |

**The per-node view does not say that it is per node.** `GET /nodes/N/replication` and
`pvesr status` on N return the same jobs: those N is the source of. On a cluster where the
three nodes are the source of 18, 18 and 24 jobs, each node answers with its own 18 or 24 and
the cluster route with 60. A coverage check made from one node there reported two thirds of
the HA guests as unreplicated when every one of them was covered.

SSH equivalents, when the API is not available: `pve-ssh.sh -n N cat /etc/pve/replication.cfg`
is the cluster-wide job list (`/etc/pve` is the cluster file system, identical on every node),
and `pve-ssh.sh -n N pvesr status` is the per-node health view. A job in the file (tab-indented
keys under the id, which is `<vmid>-<number>`):

```
local: 100-0
	target pve2
	schedule */15
	source pve1
```

One guest replicated to two nodes has two jobs (`100-0`, `100-1`).

Coverage check:

1. `GET /cluster/ha/resources` for the HA-managed guests, and `GET /cluster/resources type=vm`
   for where each runs. Only guests with disks on local storage need a replica.
2. `GET /cluster/replication` once, and group the jobs by `guest`.
3. A guest is covered for a failover to node X only when one of its jobs has `target` X.
4. For health, `GET /nodes/N/replication` on every node: `fail_count` above 0 or an old
   `last_sync` is a job that is not keeping its replica current.
5. Before reporting a gap, check the arithmetic: the jobs from the node routes must add up to
   the jobs from the cluster route (18 + 18 + 24 = 60 in the example). A count near "total
   divided by the number of nodes" means you read one node and called it the cluster.

Changing jobs:

- Create (free): `POST /cluster/replication id=<vmid>-<n> target=<node> type=local
  schedule=...` (parameter names UNVERIFIED; `pvesh usage /cluster/replication -v`), or
  `pvesr create-local-job <vmid>-<n> <target> --schedule '*/15' [--rate MB/s]` on the node that
  runs the guest. Read `GET /cluster/replication` first: a job that "is missing" in a per-node
  view usually exists.
- Run now (free): `pvesr schedule-now <id>`.
- Disable (gated): `pvesr disable <id>`, `pvesr update <id> --disable 1` or
  `PUT /cluster/replication/<id> disable=1`; the replica goes stale, so a failover would lose
  everything since the last sync. `pvesr enable <id>` is free.
- Delete (gated): `pvesr delete <id>` or `DELETE /cluster/replication/<id>`. It also removes
  the replicated volumes on the target unless `--keep` (`keep=1`) is given.
