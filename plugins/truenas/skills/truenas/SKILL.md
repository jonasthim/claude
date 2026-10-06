---
name: truenas
description: Operate, inspect and troubleshoot a TrueNAS SCALE 25.x NAS through its JSON-RPC API and SSH. Use this whenever the user mentions TrueNAS, SCALE, their NAS, ZFS pools, datasets, zvols, snapshots, scrubs, SMB/NFS/iSCSI shares, ACLs, TrueNAS apps or Docker containers on the NAS, alerts, replication, cloud sync, system updates, or midclt, even if they only say "my nas" or "the storage box". Also use it for health checks ("is my pool ok?"), capacity questions, and "why is X not working" on the NAS.
---

# TrueNAS SCALE operations

You operate a single TrueNAS SCALE 25.x system on the user's behalf. Two tools:

- **`tn.py`** (bundled): talks to the middleware over the JSON-RPC websocket API. This is the
  primary tool for reading state and making changes, because the middleware keeps its own
  database in sync and enforces validation.
- **SSH** to the box: for read-only inspection with `zpool`, `zfs`, `docker`, `journalctl`, and
  for local `midclt` when the websocket is unreachable.

```
TN="python3 ${CLAUDE_PLUGIN_ROOT}/skills/truenas/scripts/tn.py"
```

Use `$TN` as shown below. It reads `TRUENAS_HOST` and `TRUENAS_API_KEY` from the environment,
re-executes itself inside the skill's virtualenv, prints JSON to stdout and errors as JSON to
stderr. Exit codes: 0 ok, 1 API or connection error, 2 blocked by the safety gate, 3 setup problem.

## 1. Start every session with a connectivity check

```
$TN info
```

This prints version, hostname, uptime, an alert summary and running jobs. Exit code 3 means a
setup problem; the message says which:

- **Client library missing**: run `bash ${CLAUDE_PLUGIN_ROOT}/skills/truenas/scripts/setup.sh`
  yourself (it only creates a virtualenv under `~/.cache/truenas-skill` and pip-installs the
  official client from GitHub), then retry `info`.
- **`TRUENAS_HOST` / `TRUENAS_API_KEY` unset**: tell the user how to create a key (user menu →
  API Keys) and export both variables, then stop. Do not guess hostnames or ask for the key in
  chat; it belongs in the environment, not the transcript.

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

## 5. SSH for inspection and logs

The API covers state and changes; the shell is better for live diagnostics. Use
`TRUENAS_SSH_HOST` if set, otherwise `root@$TRUENAS_HOST`:

```
ssh "${TRUENAS_SSH_HOST:-root@$TRUENAS_HOST}" zpool status -v
ssh ... zfs list -r -o name,used,avail,refer,mountpoint tank
ssh ... zfs list -t snapshot -r -o name,used,creation tank/photos
ssh ... docker ps --format '{{.Names}}\t{{.Status}}'      # containers are named ix-<app>-<service>-1
ssh ... docker logs --tail 200 ix-jellyfin-jellyfin-1
ssh ... journalctl -u middlewared -n 200 --no-pager
ssh ... midclt call system.info                           # local API when the websocket is down
```

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
