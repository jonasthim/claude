# claude-truenas-skill

A [Claude Code](https://claude.com/claude-code) plugin that lets Claude operate a
**TrueNAS SCALE 25.x** system: pools, datasets, snapshots, SMB/NFS/iSCSI shares, apps,
alerts, services, updates and replication.

It talks to the middleware either over the JSON-RPC websocket API (`wss://<nas>/api/current`,
with an API key) or over SSH by running `midclt`, the middleware's own CLI, on the NAS. Both
paths are implemented in one standard-library Python script: nothing to install. SSH is also
used for read-only diagnostics (`zpool`, `zfs`, `docker logs`, `journalctl`). Destructive
operations are blocked by a code-level gate until you confirm them in the conversation.

## Install

From the marketplace in this repo (the repo is private, so the machine needs GitHub access
that can read it: a `gh auth login`, a credential helper, or an SSH key):

```
claude plugin marketplace add jonasthim/claude-truenas-skill
claude plugin install truenas@claude-truenas-skill
```

Or for local development:

```
gh repo clone jonasthim/claude-truenas-skill            # or: git clone git@github.com:jonasthim/claude-truenas-skill
claude --plugin-dir ./claude-truenas-skill
```

Requires Python 3.10+ and, for the SSH transport, an `ssh` client. No packages.

## Configure

Set up the API transport; add the SSH transport as a fallback if you want one. The API key
wins when both are present.

**API over the web port** (`ws`, primary): a key is scoped to one user, auditable, and
revocable in the UI on its own.

1. Create an API key in the TrueNAS web UI: click your user icon (top right) → *API Keys* →
   *Add*. Keys are linked to a user; a `FULL_ADMIN` user (root, truenas_admin, or any user
   with that role) can make changes, a `READONLY_ADMIN` user gives a read-only key.
2. Export in the shell you run Claude Code from:

   ```
   export TRUENAS_HOST=nas.example.lan          # hostname[:port], or a full wss:// URI
   export TRUENAS_API_KEY='1-abc...'
   ```

   Optional: `TRUENAS_USER` (the key's user, switches to `auth.login_ex`), `TRUENAS_SCHEME=ws`
   for an HTTP-only UI, `TRUENAS_VERIFY_SSL=0` for a self-signed certificate (Claude will not
   set this on its own).

**SSH + midclt** (`ssh`, fallback): for when the web port is unreachable from where Claude
runs. It runs `midclt` as root (or via passwordless sudo) on the NAS, a broader grant than an
API key, so prefer the port-forward in the next section when you can. Put your public key on
the NAS user (*Credentials → Users → edit → Authorized Keys*), enable the SSH service, then:

```
export TRUENAS_SSH_HOST=root@nas.example.lan   # a non-root user gets `sudo -n`; it must be NOPASSWD
export TRUENAS_SSH_OPTS="-J me@jump.lan"       # optional, any extra ssh options
```

Either way, `TRUENAS_SSH_HOST` is also what Claude uses for `zpool status`, `docker logs` and
similar read-only shell diagnostics. `TRUENAS_TRANSPORT=ws|ssh` (or `--transport`) forces a
choice; `TRUENAS_DEBUG=1` includes server tracebacks in error output.

Smoke test from a clone:

```
python3 skills/truenas/scripts/tn.py info
```

Offline tests (no NAS needed): `python -m unittest discover tests`. The SSH transport is
tested against a fake `ssh`; the websocket transport against `tests/fake_middleware.py`, which
needs the `websockets` package in the test interpreter (or in one named by
`TRUENAS_TEST_PYTHON`) and is skipped otherwise.

## Use

Just talk about your NAS. The skill triggers on TrueNAS, ZFS, pools, datasets, snapshots,
shares, apps, alerts and similar. Examples:

- "Is my NAS healthy? Anything I should worry about?"
- "Snapshot tank/photos, then make the photos SMB share read-only for guests."
- "Jellyfin is stuck deploying, figure out why and fix it."
- "Set up an NFS export of tank/media for 192.168.1.0/24, read-only."
- "Which snapshots on tank/backup are older than 60 days?"

## Safety model

`tn.py call` refuses methods that delete, roll back, export, wipe, lock, reboot, update the
OS, change ACLs or permissions, stop apps or services, or change users, network and boot
configuration, unless `--confirm` is passed. The skill instructs Claude to add that flag only
after you have agreed to that specific operation in the conversation. Reads, creates,
property updates, snapshots, service restarts and app starts/redeploys/upgrades run without
prompting. The pattern list is at the top of `skills/truenas/scripts/tn.py`; edit it to
taste.

Results are redacted by default: passwords, secrets, tokens, private keys, exported dataset
keys and the attributes of cloud, SSH and alert-service credentials come back as
`[REDACTED]`, with a note on stderr. `--show-secrets` (or `TRUENAS_SHOW_SECRETS=1`) turns
that off; the skill tells Claude to use it only when you explicitly ask for a credential.

## Layout

```
.claude-plugin/plugin.json      plugin manifest
.claude-plugin/marketplace.json single-plugin marketplace
skills/truenas/SKILL.md         instructions Claude loads when the skill triggers
skills/truenas/scripts/tn.py    API helper (ws or ssh transport): info | host | call | methods | jobs | query
skills/truenas/references/      cheat sheets: api-basics, storage, sharing, apps, system
tests/                          offline unit tests (python -m unittest discover tests)
evals/evals.json                example prompts for skill evaluation
```

## Reaching a NAS on another VLAN

If the NAS web port is firewalled from where you run Claude but SSH works (directly or via a
jump host), forward the port to keep the API key path: `ssh -fN -L 8443:<nas-ip>:443 <jump>`
and `TRUENAS_HOST=127.0.0.1:8443`. Expect a certificate mismatch on the forwarded name;
`skills/truenas/references/api-basics.md` lists the options, starting with connecting by a
name the certificate carries. Claude will report the certificate error and wait for your
decision rather than disable verification. The SSH transport is the fallback when you would
rather not run a tunnel.

## Compatibility and verification status

Written for TrueNAS SCALE 25.04 (Fangtooth) and 25.10 (Goldeye). The skill asks the live
system for method schemas (`core.get_methods`) before unfamiliar calls, so minor API drift
between releases is handled at run time. TrueNAS CORE and 24.10 are not supported (different
API protocol).

What has actually been exercised so far:

- Live against TrueNAS SCALE 25.10.6 over the `ws` transport (through an SSH port-forward)
  on Python 3.14: connectivity, a 49-method schema sweep against the live `core.get_methods`,
  and the read-only health queries. The references were corrected from that sweep
  (`update.run` is the updater, `update.update` only configures it; `filesystem.setperm`
  replaces the non-existent `pool.dataset.permission`; no `smart.*` namespace; `pool.scrub.run`
  is not a job).
- Offline, on Python 3.13 and 3.14: `tn.py` against `tests/fake_middleware.py` (websocket
  handshake and framing, login, queries, large responses, jobs, errors, redaction) and against
  a fake `ssh`/`midclt` (same commands, sudo handling, jump options, errors).
- Not yet live: the `ssh` transport (the test estate has no SSH key on the NAS) and any
  mutating call. Treat a reference example as a starting point and the live schema from
  `tn.py methods <name> --exact --schema` as the truth.
