# Proxmox VE

Operates Proxmox VE 9 clusters: inventory and health, VM (`qm`) and container (`pct`) lifecycle, snapshots,
vzdump backups and restores, cloud-init templates, storage, networking and SDN, cluster and HA, users, roles
and ACLs. Access goes through the REST API with an API token; SSH to the nodes is an optional second tier. The
scripts need `bash`, `curl` and `jq`.

Full reference: [plugins/proxmox/README.md](https://github.com/jonasthim/claude/blob/main/plugins/proxmox/README.md).

## Create an API token

Create the token yourself, on a node as root or in the web UI (*Datacenter → Permissions → API Tokens*), not
through Claude: the secret is shown once, and anything Claude runs lands in the transcript. A read-only token
is enough for `/proxmox:status`, `/proxmox:doctor`, listings and diagnostics:

```
pveum user add claude@pve -comment "Claude Code"
pveum user token add claude@pve ro -privsep 0 -comment "Claude read-only"
pveum acl modify / -user claude@pve -role PVEAuditor -propagate 1
```

For an operator token that can start, stop, clone, snapshot and back up guests, add:

```
pveum acl modify /vms -user claude@pve -role PVEVMAdmin -propagate 1
pveum acl modify /storage/<storage> -user claude@pve -role PVEDatastoreUser -propagate 1
pveum acl modify /sdn -user claude@pve -role PVESDNUser -propagate 1
```

Node reboot, network apply and apt need `Sys.PowerMgmt`/`Sys.Modify`, which only the Administrator role
carries (on `/` or `/nodes/<node>`), or the SSH tier.

## Configure

| Variable | Required | Purpose |
|---|---|---|
| `PVE_HOST` | yes | `host[:port]` or a URL; `https://` and `:8006` are added when absent |
| `PVE_TOKEN_ID` | yes | `user@realm!tokenid`, for example `claude@pve!ro` |
| `PVE_TOKEN_SECRET` | yes | The token value. Never printed by the scripts |
| `PVE_CA_CERT` | no | The cluster CA: copy `/etc/pve/pve-root-ca.pem` from any node |
| `PVE_INSECURE` | no | `1` disables TLS verification. A last resort you set yourself; Claude never does |
| `PVE_SSH_HOST`, `PVE_SSH_USER`, `PVE_SSH_PORT`, `PVE_SSH_KEY`, `PVE_SSH_OPTS` | no | The SSH tier |

The full table (timeouts, debug, dry run) is in the plugin README.

## Commands and what to ask

| Command | What it does |
|---|---|
| `/proxmox:doctor` | Checks the connection and token; reports what the token can do per ACL path. Run it first |
| `/proxmox:status [node \| vmid \| storage]` | Read-only cluster, node, guest or storage overview |
| `/proxmox:vm <action> <vmid>` | QEMU VM: list, status, config, start, shutdown, stop, reboot, reset, suspend, resume, clone, migrate, destroy |
| `/proxmox:ct <action> <vmid>` | LXC: list, status, config, create, start, shutdown, stop, reboot, migrate, destroy |
| `/proxmox:snapshot <vmid> <list\|create\|rollback\|delete>` | Snapshots |
| `/proxmox:backup <list\|run\|jobs\|restore\|failures>` | vzdump backups, jobs, restores, failure diagnosis |

Or just ask: "Which VMs are using the most memory?", "Snapshot 101 before I upgrade it", "Why did last night's
backup of 105 fail?". The `pve` skill loads whenever you mention Proxmox, PVE, `qm`, `pct`, vzdump or a VMID.
For a task run end to end, `@agent-proxmox:proxmox-operator` works without asking you anything, so it acts
on a gated step only when your task contains a line such as `CONFIRMED: stop vm 101`; otherwise it returns the
plan.

## Safety

Three layers:

1. **Plan and confirm.** Before a gated action (destroy, stop, rollback, migrate, deletes, node reboot,
   network or SDN apply, HA changes, cluster membership, upgrades over SSH) Claude shows a PLAN block and runs
   it only after your yes.
2. **Guard hook.** A PreToolUse hook asks for permission on every gated command, even in auto mode. There is
   no variable to disable it; disable the plugin to turn it off.
3. **Your token.** A read-only token cannot do damage whatever the model decides.

Free actions (reads, start/resume, create, clone, config changes without `delete`, snapshot create, backup
runs, staged network objects, user/role create and ACL grants) run without asking. The `pve` skill holds
the authoritative lists. See also the [safety model](safety.md).

## Troubleshooting

`/proxmox:doctor` maps each failure to a fix: exit 2 is transport or TLS (set `PVE_CA_CERT`), a 401 is a wrong
or expired token, a 403 names the ACL path and privilege that is missing.
