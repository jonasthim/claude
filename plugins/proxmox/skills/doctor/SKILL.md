---
name: doctor
description: "Slash command: check the Proxmox VE connection from this machine with four read-only API calls (version, token permissions, nodes, cluster status) plus the optional SSH tier, classify the token as read-only, operator or admin-capable, and explain how to fix each failure."
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET *)
---

# /proxmox:doctor

Read-only connection check; it takes no arguments, so ignore `$ARGUMENTS`. If the `proxmox:pve` skill is not loaded in this conversation, invoke it with the Skill tool. Never print `PVE_TOKEN_SECRET`; refer to it as "set (hidden)". Run each call below as a single plain command (no pipes, no `&&`) so the pre-approved rule applies.

## Calls, in order (stop only if call 1 fails; later failures go into the report)

1. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /version`: fields `version`, `release`, `repoid` (confirmed on a PVE 9.2 cluster). Warn when `version` does not start with `9.`; the `pve` skill documents 9.x.
2. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /access/permissions`: an object keyed by ACL path, each value `{privilege-name: 1}` (confirmed on a PVE 9.2 cluster). Collect the distinct `Family.Privilege` keys across all paths and classify the token with the table below. A failure here is a warning only.
3. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /nodes`: one item per node; print `node` and `status` (field names UNVERIFIED; print what is there).
4. `${CLAUDE_PLUGIN_ROOT}/scripts/pve-api.sh GET /cluster/status`: a mixed array with a single `type=cluster` item (`name`, `quorate`, `nodes`) and one `type=node` item per node (`name`, `online`) (confirmed on a PVE 9.2 cluster). No cluster item = standalone node; `quorate` not equal to 1 = warning. A failure here is a warning only.
5. Only when `PVE_SSH_HOST` is set: `${CLAUDE_PLUGIN_ROOT}/scripts/pve-ssh.sh --check` (255 = ssh failure, 1 = usage, no host or no ssh, anything else = the remote exit code). Otherwise report "ssh tier: not configured (set PVE_SSH_HOST for node-level commands)".

## Failures (pve-api.sh exit code plus its `pve-api:` stderr line)

| Exit | Meaning | Remedy |
|---|---|---|
| 1 | Usage, env var missing, or curl/jq missing | Export the named variable or install the tool, then rerun |
| 2 | Transport or TLS | Check `PVE_HOST` (`host[:port]`, 8006 by default), DNS and firewall. For the cluster-signed certificate copy `/etc/pve/pve-root-ca.pem` from a node and export `PVE_CA_CERT=<path>`. Only the user may opt into `PVE_INSECURE=1`; never set it yourself |
| 3 with `HTTP 401` | Token rejected | The id must be `user@realm!tokenid` and the secret must match; "access expired" means the token expired. The user recreates it in their own shell or the web UI with `pveum user token add <user@realm> <tokenid>` (it prints the secret once, so never through Claude) |
| 3 with `HTTP 403` | Token lacks a privilege | The message names the ACL path and the privilege. Grant it with `pveum acl modify <path> -user <user@realm> -role <Role> -propagate 1` (or `-token 'user@realm!tokenid'` when `privsep` is 1). Read-only: `PVEAuditor` on `/`. Operator: `PVEVMAdmin` on `/vms`, `PVEDatastoreUser` on `/storage/<id>`, `PVESDNUser` on `/sdn`. Details in `permissions.md` under the `pve` skill's references |
| 3 other, or 4 | Other API error | Read the `pve-api:` line; a 501 "no such uri" means the path or the `/api2/json` base is wrong |

## Token capability (from call 2, per ACL path)

`GET /access/permissions` is keyed by ACL path, and a privilege only holds on its path and below.
Classify each path on its own (test admin-capable first, then operator, then read-only), and
report the classes with their paths, for example `operator on /vms, read-only on /`. Never
generalise one path's privileges to the whole cluster: `Sys.Modify` on `/nodes/pve2` makes that
node admin-capable, not the token.

| Class | Test (on one path) | What works on that path |
|---|---|---|
| admin-capable | `Sys.Modify` or `Sys.PowerMgmt` present | Node power, network apply and system changes for that path (cluster-wide only on `/`); the safety contract and the guard hook are the only protection there. Say so |
| operator | Any of `VM.PowerMgmt`, `VM.Allocate`, `VM.Snapshot`, `VM.Migrate`, `Datastore.AllocateSpace` | Lifecycle, snapshots, clones and backups for the guests or storage under that path. Node reboot, network apply and apt need `Sys.PowerMgmt`/`Sys.Modify`, which only Administrator carries (on `/` or `/nodes/<node>`), or the SSH tier |
| read-only | Every privilege ends in `.Audit` | Status, listings and task logs. Power, snapshot create, backup run and destroy return 403. An empty storage content list may be a privilege gap (see the `pve` skill pitfalls) |
| limited | None of the above | List the privileges seen and point to `permissions.md` under the `pve` skill's references |

A PVEAuditor token on `/` shows exactly `Datastore.Audit Mapping.Audit Pool.Audit SDN.Audit Sys.Audit VM.Audit VM.GuestAgent.Audit` (confirmed on a PVE 9.2 cluster).

## Report

One table `check | result | detail` with the rows `version`, `token`, `nodes`, `cluster`, `ssh`; result is `ok`, `warn`, `fail` or `skipped`. Add a `warn` row `tls` whenever `PVE_INSECURE=1` is set (`PVE_CA_CERT` is the fix). Below the table print exactly one line: `ready for <class> tasks on <paths>` (one entry per class, for example `ready for operator tasks on /vms; read-only on /`) or `next step: <the single remedy>`.
