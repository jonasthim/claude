---
name: truenas
description: Operate, inspect and troubleshoot a TrueNAS SCALE 25.x NAS through its JSON-RPC API and SSH. Use this whenever the user mentions TrueNAS, SCALE, their NAS, ZFS pools, datasets, zvols, snapshots, scrubs, SMB/NFS/iSCSI shares, ACLs, TrueNAS apps or Docker containers on the NAS, alerts, replication, cloud sync, system updates, or midclt, even if they only say "my nas" or "the storage box". Also use it for health checks ("is my pool ok?"), capacity questions, and "why is X not working" on the NAS.
---

# TrueNAS SCALE operations

You operate a single TrueNAS SCALE 25.x system on the user's behalf. Two tools:

- **`tn.py`** (bundled, standard library only, nothing to install): talks to the middleware
  API. This is the primary tool for reading state and making changes, because the middleware
  keeps its own database in sync and enforces validation. It has two transports that behave
  identically. `ws` (JSON-RPC over the web port with an API key) is the primary one: the key
  is scoped to a user, auditable, and revocable on its own. `ssh` (runs `midclt`, the
  middleware's own CLI, on the NAS) is the fallback for when the web port cannot be reached;
  it needs root or passwordless sudo on the NAS, which is a larger grant. `tn.py` picks `ws`
  when `TRUENAS_API_KEY` is set and `ssh` otherwise; `TRUENAS_TRANSPORT` or `--transport`
  overrides.
- **SSH shell commands** on the box: for read-only inspection with `zpool`, `zfs`, `docker`,
  `journalctl`.

```
TN="python3 ${CLAUDE_PLUGIN_ROOT}/skills/truenas/scripts/tn.py"
```

Use `$TN` as shown below. It reads its connection from the environment, prints JSON to
stdout and errors as JSON to stderr. Exit codes: 0 ok, 1 API or connection error, 2 blocked by
the safety gate, 3 setup problem.

## 1. Start every session with a connectivity check

```
$TN info
```

This prints the transport in use, version, hostname, uptime, an alert summary and running
jobs. Exit code 3 means nothing is configured: tell the user the two options (an API key plus
`TRUENAS_HOST` for `ws`, or `TRUENAS_SSH_HOST` for `ssh`; both in the README) and stop. Do
not guess hostnames or ask for the key in chat; it belongs in the environment, not the
transcript.

Exit code 1 from `info` is a connection problem; the JSON names a `cause`:

- **`timeout`**: the NAS web port is on a network this machine cannot reach (typical when the
  NAS sits on a server VLAN and the workstation is firewalled from it). Do not keep retrying.
  Tell the user the two ways through, from `references/api-basics.md` ("Transports"): keep
  the API key and forward the web port over SSH, or fall back to `--transport ssh`, which
  works today if SSH to the NAS is set up but runs as root on the box. Let them pick.
- **`certificate`**: the NAS uses a self-signed certificate, or the name you connect through
  is not on it. Report what the error says and stop; connecting by the name on the
  certificate, trusting the NAS CA, or disabling verification are the user's calls, never
  yours. The `ssh` fallback sidesteps TLS if the user prefers that trade.
- **`refused`** or **`dns`**: wrong port or hostname, or the middleware is down. If SSH works,
  `midclt call system.info` on the box tells the two apart.
- **`ssh`**: ssh itself failed (key, host, jump). The ssh error text is in the message.

Note the version from `info`. 25.04 and 25.10 differ in a few argument shapes (SMB share
options, update methods), which is why the next rule exists.

## 2. Discover before you call

The middleware is self-describing. Before calling any method whose arguments you are not
certain of, look at its schema:

```
$TN methods pool.dataset.create --schema
$TN methods sharing.smb            # list everything in a namespace
```

The output marks methods that run as jobs (`"job": true`) and methods that need `--confirm`.
Guessing argument names wastes a round trip and produces validation errors, so one `methods`
call first is almost always cheaper. The references in this skill list the common methods and
argument shapes; treat them as a map, and the live schema as the truth.

Nearly every namespace has `.query`, which takes query-filters and query-options:

```
$TN query pool.dataset --filter name~^tank/ --select name,used.parsed,available.parsed
$TN query alert --filter dismissed=false
$TN query app --filter state!=RUNNING --select name,state,upgrade_available
$TN call pool.dataset.get_instance tank/photos
```

Direct calls take positional JSON arguments. A bare word that is not valid JSON is passed as a
string, so `tank/photos` works without quoting; objects need quotes:

```
$TN call pool.snapshot.create '{"dataset": "tank/photos", "name": "before-acl-change"}'
$TN call service.restart cifs
```

## 3. Jobs

Long-running methods return a job id instead of a result: scrubs, app install/upgrade/
redeploy, replication runs, updates, recursive deletes, ACL changes, pool operations. Pass
`--job` so `tn.py` waits, streams progress to stderr, and prints the final result:

```
$TN call app.upgrade jellyfin --job
$TN jobs --running
$TN jobs --id 1234          # full record including error and traceback
```

If a job fails, `jobs --id` shows the server-side exception. Quote the relevant line to the
user rather than the whole traceback.

## 4. Safety policy

The user chose "confirm destructive operations only". In practice:

**Run freely:** every read, `*.create`, `*.update` on datasets and shares, snapshot creation,
`service.start` / `service.restart` / `service.reload`, `app.start` / `app.redeploy` /
`app.upgrade`, `pool.scrub.run`, `alert.dismiss`.

**Ask first, then add `--confirm`:** anything that deletes, rolls back, exports, wipes, locks,
reboots, updates the OS, changes ACLs or permissions, stops an app or service, changes users,
network or boot configuration. `tn.py` refuses these with exit code 2 until `--confirm` is
present. Add the flag only after the user has agreed to *that specific operation* in this
conversation. State what will run, on what, and what cannot be undone. For example:

> This will run `pool.snapshot.rollback tank/photos@daily-2026-10-01`, discarding every change
> to tank/photos since that snapshot (3 newer snapshots will also be destroyed). Go ahead?

A prior "yes" to a different operation does not carry over. If the user asked for the
destructive action in their original message with enough specificity ("delete the snapshot
tank/photos@test"), that counts as confirmation; a vague request ("clean up old snapshots")
does not, so list the candidates and ask.

Habits that make the gate rarely matter:
- Take a snapshot before changing dataset properties, share ACLs, or upgrading an app whose
  config lives on a dataset. Snapshots are cheap; rollbacks are not.
- Prefer the API over raw `zfs destroy` / `zfs set` over SSH. The middleware tracks datasets,
  shares and apps in its database; raw ZFS changes leave it stale.
- Never pass `--insecure` or set `TRUENAS_VERIFY_SSL=0` on your own. If `info` fails with a
  certificate error, tell the user and let them decide.
- On errors, read the validation message. The middleware says exactly which field is wrong.
- `tn.py` replaces credential-looking values (passwords, secrets, tokens, private keys, dataset
  encryption keys, cloud and SSH credential attributes) with `[REDACTED]` and says so on
  stderr. Pass `--show-secrets` only when the user has explicitly asked for that credential,
  and remember that whatever you print lands in the transcript.

## 5. SSH for inspection and logs

The API covers state and changes; the shell is better for live diagnostics. Use
`TRUENAS_SSH_HOST` if set; otherwise build the target from the bare hostname (`TRUENAS_HOST`
may carry a scheme or port, which ssh would misread, so never paste it into ssh directly).
When `tn.py` is already using the `ssh` transport, this is the same connection:

```
SSH_TARGET="${TRUENAS_SSH_HOST:-root@$($TN host)}"
ssh "$SSH_TARGET" zpool status -v
ssh "$SSH_TARGET" zfs list -r -o name,used,avail,refer,mountpoint tank
ssh "$SSH_TARGET" zfs list -t snapshot -r -o name,used,creation tank/photos
ssh "$SSH_TARGET" docker ps --format '{{.Names}}\t{{.Status}}'   # containers are named ix-<app>-<service>-1
ssh "$SSH_TARGET" docker logs --tail 200 ix-jellyfin-jellyfin-1
ssh "$SSH_TARGET" journalctl -u middlewared -n 200 --no-pager
ssh "$SSH_TARGET" midclt call system.info                        # what the ssh transport does under the hood
```

Add `$TRUENAS_SSH_OPTS` (for example `-J jump.lan`) to these commands when it is set, since
`tn.py` uses it too. When the API goes through a port-forward, `TRUENAS_SSH_HOST` should point
at the real NAS, not at the forwarded address.

Keep SSH read-only unless the user asks for a shell-level change and the API has no equivalent.
If SSH is not configured, say so and continue with the API alone.

## 6. Reporting back

Lead with health, then what you changed, then what is blocked or needs a decision. Numbers
belong in a short table (pool capacity, snapshot counts), not in prose. When you created or
changed something, name it exactly as TrueNAS shows it (dataset path, share name, app name).
When something is gated, show the exact method and arguments you would run so the user can
say yes to that.

## Reference map

Open the one that matches the task; each is a cheat sheet of methods, argument shapes, SSH
equivalents and gotchas for that area. They live in
`${CLAUDE_PLUGIN_ROOT}/skills/truenas/references/`.

| Task | Read |
|---|---|
| Connection, auth, versions, query syntax, errors | `references/api-basics.md` |
| Pools, datasets, zvols, snapshots, scrubs, quotas, encryption | `references/storage.md` |
| SMB, NFS, iSCSI shares, ACLs and permissions | `references/sharing.md` |
| Apps (Docker), container logs, custom compose apps | `references/apps.md` |
| Alerts, services, updates, boot environments, replication, cloud sync, SMART | `references/system.md` |
