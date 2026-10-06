# proxmox: a Claude Code plugin for Proxmox VE 9

Operate a Proxmox VE 9.x cluster from Claude Code: inventory and health, VM and LXC container lifecycle,
snapshots, vzdump backups and restores, cloud-init templates, storage, networking and SDN, cluster, HA and
node maintenance, users, roles and API tokens. Access goes through the REST API with an API token; SSH to the
nodes is an optional second tier for what the API cannot do. Every destructive or disruptive action is
planned, confirmed by you, and additionally caught by a PreToolUse guard hook.

## Requirements

- Claude Code with plugin support (`claude plugin ...` commands).
- `bash`, `curl` and `jq` on the machine running Claude Code; `ssh` only for
  the SSH tier; `python3` only for the test suite.
- A Proxmox VE 9.x cluster reachable on port 8006 and an API token.

## Install

From the marketplace in this repository:

```
claude plugin marketplace add jonasthim/claude-proxmox-skill
claude plugin install proxmox@jonasthim
```

For development, load the checkout for one session without installing:

```
claude --plugin-dir ./
```

After editing skills, agents or hooks run `/reload-plugins` inside Claude Code.

## Configuration

Export these variables in the shell that starts Claude Code. Never put the secret into a skill, a prompt or a
committed file.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `PVE_HOST` | yes | | Node address, `host` or `host:port`; `https://` and `:8006` are added when missing. A value with `://` is used verbatim |
| `PVE_TOKEN_ID` | yes | | `user@realm!tokenid` |
| `PVE_TOKEN_SECRET` | yes | | Token value shown once at creation. Never printed by the scripts |
| `PVE_CA_CERT` | no | | Path to the CA that signed the node certificate (`--cacert`). Every cluster has it at `/etc/pve/pve-root-ca.pem` on any node; see TLS below |
| `PVE_INSECURE` | no | unset | `1` disables TLS verification (`curl -k`) and prints one warning. Opt in yourself; Claude never sets it |
| `PVE_TIMEOUT` | no | `30` | curl `--max-time` in seconds |
| `PVE_API_RAW` | no | unset | `1` prints the full `{"data": ...}` envelope instead of `.data` |
| `PVE_API_DEBUG` | no | unset | `1` prints method and URL on stderr (never the header) |
| `PVE_SSH_HOST` | no | host part of `PVE_HOST` | Node for the SSH tier |
| `PVE_SSH_USER` | no | `root` | SSH user |
| `PVE_SSH_PORT` | no | `22` | SSH port |
| `PVE_SSH_KEY` | no | | Private key file (`ssh -i`) |
| `PVE_SSH_OPTS` | no | | Extra `ssh` options |

### TLS

Node certificates are issued by the cluster's own CA (`PVE Cluster Manager CA`), so verification works without
any insecure flag: copy `/etc/pve/pve-root-ca.pem` from any node to the machine that runs Claude Code and export
`PVE_CA_CERT=<path>`. The CA file is public; only the node keys are secret. `PVE_INSECURE=1` exists for a
node you cannot copy the CA from; it is a last resort, and Claude never sets it on its own. (CA path confirmed
on a PVE 9.x cluster; it is not in the condensed reference notes.)

### Create an API token

If a token with `PVEAuditor` on `/` already exists (for example one used by a monitoring exporter), reuse it for
the read-only checks; creating a user is a permanent change to the cluster. Otherwise, on a node, as root. The
read-only recipe is enough for `/proxmox:status`, `/proxmox:doctor`, listings and diagnostics:

```
pveum user add claude@pve -comment "Claude Code"
pveum user token add claude@pve ro -privsep 0 -comment "Claude read-only"
pveum acl modify / -user claude@pve -role PVEAuditor -propagate 1
```

`pveum user token add` prints the full token id (`claude@pve!ro`) and the value once; it cannot be retrieved
later. With `-privsep 0` the token has the same permissions as the user. With `-privsep 1` (the default) the
token needs its own ACLs: `pveum acl modify / -token 'claude@pve!ro' -role PVEAuditor`. The privilege-separated
(`-privsep 1`) variant of both recipes is in `skills/pve/references/permissions.md`.

For an operator token that can start, stop, clone, snapshot and back up guests, add to the user (or token):

```
pveum acl modify /vms -user claude@pve -role PVEVMAdmin -propagate 1
pveum acl modify /storage/<storage> -user claude@pve -role PVEDatastoreUser -propagate 1
pveum acl modify /sdn -user claude@pve -role PVESDNUser -propagate 1
```

Node reboot, network apply and apt need `Sys.PowerMgmt` and `Sys.Modify`, which are root-only tier privileges
(whether a non-root user or token can hold Sys.PowerMgmt/Sys.Modify is UNVERIFIED; see references/permissions.md
in the `pve` skill); use the SSH tier or a root@pam token for those. Then export the variables, for example:

```
export PVE_HOST=pve1.example.net:8006
export PVE_TOKEN_ID='claude@pve!ro'
export PVE_TOKEN_SECRET='<value printed by pveum>'
export PVE_CA_CERT=/path/to/pve-root-ca.pem
```

Run `/proxmox:doctor` first in every new setup.

## Safety model

Three layers:

1. The `pve` skill's safety contract. Before any action in the gated set the
   model prints a PLAN block (Target, Current state, Action with the exact
   call, Effect, Revert, "Reply yes to proceed"), stops, and runs the action
   only after you say yes in a later message.
2. The guard hook (`hooks/hooks.json` runs `scripts/guard.sh` before every
   Bash call). When a command matches a gated pattern the hook returns
   `permissionDecision: ask` with a reason such as `Proxmox guard:
   hard-stops the guest without graceful shutdown ...`, so Claude Code shows
   a permission prompt even in auto mode. The hook never allows or denies on
   its own.
3. Your token's privileges. A read-only token cannot do damage whatever the
   model decides.

There is no environment variable that disables the guard; that is by design, because a model-issued export
cannot reach the hook process but a dotfile variable would silently remove the only backstop. Approve the
prompt to proceed, or disable the plugin to turn the guard off. In headless runs (`claude -p`) an `ask`
becomes a deny with the reason shown, which is the intended outcome for unattended destructive commands.
Behaviour of `ask` under `bypassPermissions` is UNVERIFIED.

The subagent cannot prompt, so it runs a gated action only when its task message contains `CONFIRMED: <action>
<target>`; otherwise it returns the PLAN block and stops. The hook still fires inside it.

### Gated set (plan, confirm, then guard prompt)

destroy; stop/reset/shutdown/reboot/suspend of a guest; snapshot rollback/delete; migrate (guest or HA); template conversion; disk/volume move/unlink or `delete=` on config; backup/volume deletion (`pvesm free|remove|prune-backups`, `pveam remove`, `prunebackups`, `vzdump --remove 1`/`--prune-backups`, `rm` of `vzdump-*`); any `DELETE`/`pvesh delete`; node reboot/shutdown/stopall/migrateall/suspendall; network apply (`PUT /nodes/{node}/network`, `ifreload`/`ifdown`/`ifup` over SSH); SDN apply/rollback (`PUT /cluster/sdn`, `/cluster/sdn/rollback`); HA `remove|set|migrate|relocate|crm-command`, `rules set|remove`, `disarm-ha`, HA resource `state=stopped|disabled`; `pvecm delnode|expected|add|create|qdevice`; bulk shutdown/suspend/migrate; `apt upgrade/dist-upgrade/full-upgrade/remove/purge/autoremove` over SSH; `systemctl stop|restart|reboot|poweroff|halt|isolate`, `reboot|shutdown|poweroff|halt|init 0|init 6` over SSH.

### Free set (runs without confirmation)

all GETs; start/resume; create VM/CT; clone; set config without `delete`; snapshot create; vzdump run without explicit prune; create backup job; HA resource add; SDN/network object create/edit (staged, not applied); storage add/edit; user/role/token create; apt update (refresh); task log reads.

## Commands

| Command | Arguments | What it does |
|---|---|---|
| `/proxmox:pve` | `[task or question]` | Main knowledge skill; also loads automatically when you mention Proxmox, PVE, qm, pct, vzdump or a VMID |
| `/proxmox:status` | `[node \| vmid \| storage]` | Read-only cluster overview, node detail with failed tasks, or guest detail |
| `/proxmox:doctor` | | Checks tools, env, TLS, API version, token capabilities, nodes, optional SSH tier; explains exit codes |
| `/proxmox:vm` | `<action> <vmid> [key=value ...]` | QEMU VM: list, status, config, start, shutdown, stop, reboot, reset, suspend, resume, clone, migrate, destroy |
| `/proxmox:ct` | `<action> <vmid> [key=value ...]` | LXC: list, status, config, create, start, shutdown, stop, reboot, migrate, destroy |
| `/proxmox:snapshot` | `<vmid> <list\|create\|rollback\|delete> [snapname]` | Snapshots for VMs and containers |
| `/proxmox:backup` | `list [vmid] \| run <vmid> \| jobs \| restore <vmid> <volid> \| failures` | vzdump backups, jobs, restores and failure diagnosis |

Only `pve` is model-invocable; the other skills run when you type the command.

## Subagent

`@agent-proxmox:proxmox-operator` runs a task end to end with the `pve` skill preloaded, using Bash, Read and
Grep. Because a subagent cannot ask you anything, include a confirmation line in the task for each gated step:

```
@agent-proxmox:proxmox-operator stop VM 101 on whichever node it runs.
CONFIRMED: stop vm 101
```

Without the line the agent returns the PLAN block instead of acting. Its report lists every call, each UPID
with its exit status, the verified state afterwards and the revert path.

## Scripts

All scripts live in `scripts/`, need only bash, curl and jq (plus ssh for `pve-ssh.sh`), print usage with
`-h`, and never print `PVE_TOKEN_SECRET`.

### Why ship scripts at all

A skill can teach Claude the API and let it compose `curl` itself, and that degrades more gracefully when the
API changes. Each script here exists because it provides a property that an instruction cannot:

- `pve-api.sh`: the token secret never enters a command line. A hand-written `curl` puts the `Authorization`
  header into the command string, which lands in the session transcript and in any pasted report. The script
  reads `PVE_TOKEN_SECRET` from the environment and builds the header internally. It also gives every call a
  fixed shape (`pve-api.sh METHOD /path key=value`), which is what makes a method-scoped permission rule such
  as `Bash(*/scripts/pve-api.sh GET *)` possible.
- `guard.sh`: the hook matches on command text. Rules can only be reliable against a fixed command shape; against
  free-form `curl`, where `-X DELETE` and a URL built from variables can sit anywhere, a text guard can be evaded
  by rephrasing, and a guard that looks like protection but is not is worse than none.
- `pve-task.sh`: polling a UPID, paging the log and mapping `exitstatus` to an exit code is deterministic work
  that Claude would otherwise re-implement, slightly differently, on every call.
- `pve-doctor.sh`: a fixed first-contact check with stable exit codes (1 env, 2 TLS, 3 401, 4 403) that the
  `/proxmox:doctor` skill can map to remedies, and that a human can run without Claude to rule the plugin in or
  out when something fails. It is the script most easily replaced by prose; keep it only while that holds.
- `pve-ssh.sh`: quoting remote arguments correctly and keeping host, user, port and key in one place.

If the alternative to a script is prose that works as well, prefer the prose.

| Script | Usage | Exit codes |
|---|---|---|
| `pve-api.sh` | `pve-api.sh <GET\|POST\|PUT\|DELETE> <path> [key=value ...]`; GET/DELETE params go to the query string, POST/PUT to a form body; prints `.data` (bare UPID string or pretty JSON) | 0 2xx; 1 usage, missing env, missing curl or jq; 2 transport or TLS; 3 HTTP 4xx; 4 HTTP 5xx or any other non-2xx/non-4xx status |
| `pve-task.sh` | `pve-task.sh <UPID\|-> [--timeout SECS] [--interval SECS] [--no-log]`; polls the task, prints the log and a final `exitstatus: <value>` line | 0 OK or WARNINGS; 1 task failed; 2 API or transport error; 3 usage or bad UPID; 4 timeout |
| `pve-doctor.sh` | `pve-doctor.sh`; lines prefixed `[ok]`, `[warn]`, `[fail]`, `[info]` | 0 ok; 1 prerequisite or env; 2 transport or TLS; 3 HTTP 401; 4 HTTP 403; 5 other API error |
| `pve-ssh.sh` | `pve-ssh.sh [-n HOST] [--check] <command> [args...]`; `--check` runs `pveversion` | remote exit code; 1 usage or no host; 255 ssh failure |
| `guard.sh` | PreToolUse hook; reads the tool input JSON on stdin and prints an `ask` decision for gated commands | always 0 |

Error lines on stderr look like `pve-api: HTTP 403 DELETE /nodes/pve1/qemu/100: Permission check failed ...`.

## SSH tier

Some operations have no API endpoint: node maintenance mode (`ha-manager crm-command node-maintenance
enable|disable <node>`), package upgrades (`apt dist-upgrade`), cluster join and leave (`pvecm`), hand edits
of `/etc/network/interfaces` followed by `ifreload -a`, `pct enter|exec|push|pull`, `pvereport`, `pve8to9` and
`qm showcmd`. Set `PVE_SSH_HOST` (and `PVE_SSH_USER`, `PVE_SSH_PORT`, `PVE_SSH_KEY`, `PVE_SSH_OPTS` as
needed), use key-based authentication (the script runs ssh with `BatchMode=yes`), and verify with `pve-ssh.sh
--check`. The guard hook covers the gated commands when they are sent through `ssh` or `pve-ssh.sh`.

## Testing

```
bash tests/run.sh
```

Starts a mock Proxmox API (python3 standard library) on a free port, runs the script tests, the guard rule
table (`tests/guard_cases.txt`), the plugin lint (`tests/lint_plugin.py`) and, when available, shellcheck and
`claude plugin validate .`. No real cluster is touched.

## Provenance and accuracy

Endpoints, parameters, CLI flags and privilege names were condensed from the Proxmox VE 9.x sources and
documentation. Items that could not be confirmed are marked `UNVERIFIED` in the skill and references. Proxmox
changes between minor releases; when something differs, trust your cluster: the full API viewer and
documentation for your exact version are served by every node at `https://<node>:8006/pve-docs/`, and `pvesh
usage <path> -v` shows the live schema.

## License

MIT, see `LICENSE`.
