---
name: doctor
description: "Slash command: check the Proxmox VE connection from this machine (tools, environment variables, TLS, API version, token capabilities, nodes, cluster status, optional SSH tier) and explain how to fix each failure."
argument-hint: ""
disable-model-invocation: true
---

# /proxmox:doctor

If the `proxmox:pve` skill is not loaded in this conversation, invoke it with the Skill tool and apply its safety contract. This command is read-only. Ignore `$ARGUMENTS`; the doctor takes none.

Run exactly one command:

```
${CLAUDE_PLUGIN_ROOT}/scripts/pve-doctor.sh
```

Show the user the `[ok]`, `[warn]`, `[fail]` and `[info]` lines as they are. Never print `PVE_TOKEN_SECRET`; the script itself prints the secret only as `set (hidden)`.

## Interpret the exit code

| Exit | Meaning | Remedy |
|---|---|---|
| 0 | Everything the script could check is fine | Continue with `/proxmox:status` |
| 1 | Prerequisite or environment problem (curl or jq missing, `PVE_HOST`, `PVE_TOKEN_ID` or `PVE_TOKEN_SECRET` unset, `PVE_CA_CERT` file missing) | Install the missing tool or export the named variable and run the doctor again |
| 2 | Transport or TLS failure (host unreachable, wrong port, certificate not trusted) | Check `PVE_HOST` (host or host:port, port 8006 by default), firewall and DNS; for the cluster-signed certificate copy `/etc/pve/pve-root-ca.pem` from a node and export `PVE_CA_CERT=<path>`; only if that is impossible and the user accepts the risk, `PVE_INSECURE=1`. Never set `PVE_INSECURE=1` yourself |
| 3 | HTTP 401: the API rejected the token | The token id must have the form `user@realm!tokenid` and the secret must match; expired tokens report "access expired". Recreate the token on a node with `pveum user token add <user@realm> <tokenid>` |
| 4 | HTTP 403: the token authenticates but lacks privileges | Give the user or token a role via `pveum acl modify <path> -user <user@realm> -role <Role> -propagate 1` (or `-token 'user@realm!tokenid'` when `privsep` is 1). Read-only: `PVEAuditor` on `/`. Operator: `PVEVMAdmin` on `/vms`, `PVEDatastoreUser` on `/storage/<id>`, `PVESDNUser` on `/sdn` |
| 5 | Any other API error (4xx other than 401/403, or 5xx) | Read the `pve-api:` error line; a 501 "no such uri" usually means the API path or the `/api2/json` base is wrong |

## Interpret the capability summary

The script lists privilege names found in `GET /access/permissions` (an object keyed by ACL path, each value `{privilege-name: 1}`; confirmed on a PVE 9.2 cluster) and classifies the token:

- read-only (audit privileges only): `/proxmox:status`, `/proxmox:snapshot <vmid> list`, `/proxmox:backup list` and `failures` work; power, snapshot create, backup run and destroy will return 403. A PVEAuditor token on `/` shows exactly these seven: `Datastore.Audit Mapping.Audit Pool.Audit SDN.Audit Sys.Audit VM.Audit VM.GuestAgent.Audit`, reported as `[ok] token capability: read-only (7 distinct privileges seen)` (confirmed on a PVE 9.2 cluster). Such a token may also get an empty storage content list from a storage that is not empty; see the `pve` skill pitfalls.
- operator (VM.PowerMgmt, VM.Snapshot, VM.Allocate, VM.Migrate, Datastore.AllocateSpace): VM/CT lifecycle, snapshots, clones and backups work; node reboot, network apply and apt need `Sys.PowerMgmt` and `Sys.Modify`, which only the Administrator role carries (not PVEVMAdmin or PVEAuditor): the user, group or token must hold Administrator on `/` or on `/nodes/<node>` (a non-root principal can; confirmed on a PVE 9.2 cluster), or the SSH tier is the alternative.
- admin-capable: everything, so the guard hook and the confirmation rule in the safety contract are the only protection. Say so.

## Warnings worth repeating

- `[warn]` major version is not 9: the `proxmox:pve` skill documents 9.x; endpoints or privileges may differ.
- `[warn]` `PVE_INSECURE=1`: TLS verification is off; recommend `PVE_CA_CERT` instead.
- `[info]` SSH tier skipped: `PVE_SSH_HOST` or `PVE_SSH_USER` is unset; only needed for node maintenance, apt upgrades, cluster join/leave and `pct enter/exec`.

End with one line: ready for read-only, operator or admin tasks, or the single next step to fix.
