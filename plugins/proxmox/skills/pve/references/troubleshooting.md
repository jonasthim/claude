# Troubleshooting

Condensed from Proxmox 9.x sources (pve-http-server, pve-manager, pve-docs) and the
plugin's script contracts; items marked UNVERIFIED were not confirmed.

## HTTP codes and what to do

| Code | Typical message | Cause | Do |
|---|---|---|---|
| 400 | `errors: {<param>: <msg>}` | parameter validation; `nextid` taken | fix the named parameter; re-run `GET /cluster/nextid` |
| 401 | `authentication failure` (UNVERIFIED wording); `token '<id>' access expired` | wrong `PVE_TOKEN_ID`/`PVE_TOKEN_SECRET`, expired token | run `pve-doctor.sh`; ask the user for a new token; never guess credentials |
| 403 | `Permission check failed (/vms/109, VM.PowerMgmt)`: names the ACL path and the privilege exactly (confirmed on a PVE 9.2 cluster) | token lacks the role, or `privsep=1` token without its own ACL | map the privilege with `permissions.md`; do not escalate on your own |
| 500 | `VM already running`, `VM <N> already exists`, `no such task` | state conflict or uncaught server error | re-discover state, re-plan; read the task log if a UPID exists |
| 501 | `no such uri`; body on GET/DELETE | wrong path or method; a body sent with GET/DELETE | check the path in `api-cheatsheet.md`; use `pve-api.sh`, which never sends a body on GET/DELETE |
| 506 | upload content type | wrong `content=` on upload | use `iso`, `vztmpl` or `import` |

Auth failures are delayed 3 s server-side; a slow 401 is normal, not a timeout.
`/api2/extjs` answers 200 on everything with `success:0`; if a call "succeeds" but nothing
happened, check the base path is `/api2/json`.

## Task problems

- A call returned a UPID but the change did not happen: `pve-task.sh <UPID>` (or
  `GET /nodes/N/tasks/UPID/status` and `.../log`). `exitstatus` must be `OK` or
  `WARNINGS: n`; anything else is the error text.
- Find recent failures: `GET /nodes/N/tasks errors=1 limit=20` (filters `typefilter`,
  `vmid`, `userfilter`, `source active|archive|all`, `since`, `until`); cluster-wide
  `GET /cluster/tasks`. CLI: `pvenode task list --errors --vmid 100`, `pvenode task log <upid>`.
- Task list items carry `endtime, id, node, pid, pstart, starttime, status, type, upid, user`;
  `status` is the task's final status text, `OK` on success, otherwise the error message
  itself (e.g. `cannot remove protected volume ...`), not an enum (confirmed on a PVE 9.2
  cluster). Quote it as the failure reason before reading the log.
- Stop a stuck task: `DELETE /nodes/N/tasks/UPID` (gated). Task logs on disk:
  `/var/log/pve/tasks/`.
- `pve-task.sh` exit 4 (timeout) does not mean the task failed; it is still running.
  Re-poll with a longer `--timeout` before planning anything else on that guest.

## Guest locked or inconsistent

- `GET .../status/current` shows `lock` (backup, snapshot, migrate, clone, ...). Wait for
  the owning task; only after it is really gone use `qm unlock <vmid>` / `pct unlock`
  over SSH (ask first; `skiplock` on the API is root@pam only).
- Pending config: `GET .../config` shows pending values by default; `current=1` shows the
  live config; `GET .../pending` lists `{key, value, pending, delete}`. Pending changes
  apply on the next stop/start (reboot from inside the guest is not enough; UNVERIFIED).
  Undo a pending change with `revert=<keys>` on `PUT .../config`.
- Digest conflict on `PUT|POST .../config` or `resize`: the config changed since you read
  it. Re-read `GET .../config`, re-check your plan, pass the new `digest`.
- `GET /cluster/nextid` returned an id that then "already exists": another creator won the
  race; call `nextid` again.
- Destroy refused for an HA-managed or replicated guest: add `purge=1` (still gated).
- CT live migration refused: running containers need `restart=1`.
- Clone of a running CT refused: full clones of running containers only work from a
  snapshot (`snapname=`).

## Node and service checks

```
pve-ssh.sh -n N pveversion -v
pve-ssh.sh -n N pvecm status
pve-ssh.sh -n N systemctl status pvedaemon pveproxy pvestatd pvescheduler spiceproxy
pve-ssh.sh -n N journalctl -eu pve-ha-crm
pve-ssh.sh -n N qm showcmd V --pretty        # the exact QEMU command line
pve-ssh.sh -n N pvereport                    # full support report (large)
pve-api.sh GET /nodes/N/syslog
pve-api.sh GET /nodes/N/journal
```

Daemons: `pvedaemon` (API worker), `pveproxy` (port 8006), `pvestatd` (status
collection), `spiceproxy`, `pvescheduler` (jobs). Unit names `pve-cluster`, `pve-ha-lrm`
and `corosync` are UNVERIFIED (standard names). `systemctl restart|stop` of any of them is
gated. UNVERIFIED (standard behaviour): cluster-wide config under `/etc/pve` turns
read-only when the node loses quorum (`pvecm status`), so every write fails until quorum
returns.

## Storage content list empty

An empty `GET /nodes/N/storage/S/content` list (with or without `content=backup`) from a
storage whose `GET /nodes/N/storage` entry shows non-zero `used` (or whose
`/cluster/resources` item shows non-zero `disk`) is suspect. On a PVE 9.2 cluster a
PVEAuditor token got HTTP 200 and `[]` from a storage with 870 GiB used that holds 431
backup volumes per `pvesm list` on the node; the cause (a missing privilege or an API
filter) is UNVERIFIED. Report "could not enumerate contents with this token" rather than
"no backups" or "no content", and suggest `pvesm list S` over the SSH tier or a token with
more privileges. Never conclude that no backups exist from an empty list alone.

## Network lockout prevention

Before `PUT /nodes/N/network`: confirm the management IP and gateway stay on a bridge
that keeps its physical port, keep a console path (IPMI, physical) in the PLAN's revert
line, and prefer `DELETE /nodes/N/network` (revert staged) over a second apply when the
first one looks wrong.

## Script exit codes

| Script | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| `pve-api.sh` | 2xx | usage, missing env var, missing curl/jq | transport/TLS (curl failed) | HTTP 4xx | HTTP 5xx or any other non-2xx/non-4xx status | |
| `pve-task.sh` | task OK or WARNINGS | task failed (see log) | API/transport error | usage or bad UPID | timeout, task still running | |
| `pve-doctor.sh` | all checks ok | prerequisite or env | transport/TLS | HTTP 401 | HTTP 403 | other API error |
| `pve-ssh.sh` | remote exit 0 | usage or no host | | | | (255 = ssh failure; otherwise the remote exit code) |

Error lines: `pve-api: HTTP <code> <METHOD> <path>: <message>` then `  <param>: <msg>`
per entry in `errors`; `pve-api: curl failed (<exit>): <stderr>` for transport problems.
Example (exit 3; confirmed on a PVE 9.2 cluster):
`pve-api: HTTP 403 POST /nodes/<node>/lxc/109/status/stop: Permission check failed (/vms/109, VM.PowerMgmt)`.
`PVE_API_DEBUG=1` prints method and URL (never the header). TLS errors: set `PVE_CA_CERT`
to the cluster CA; do not set `PVE_INSECURE=1` yourself.
