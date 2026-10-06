# claude-truenas-skill

A [Claude Code](https://claude.com/claude-code) plugin that lets Claude operate a
**TrueNAS SCALE 25.x** system: pools, datasets, snapshots, SMB/NFS/iSCSI shares, apps,
alerts, services, updates and replication.

It talks to the middleware over the JSON-RPC websocket API (`wss://<nas>/api/current`) using
the official `truenas_api_client`, and uses SSH for read-only diagnostics (`zpool`, `zfs`,
`docker logs`, `journalctl`). Destructive operations are blocked by a code-level gate until
you confirm them in the conversation.

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

The helper needs the official Python client, which is not on PyPI. The first time the skill
runs it will install it into a private virtualenv (`~/.cache/truenas-skill/venv`, override
with `TRUENAS_VENV`). To do that yourself ahead of time, run `skills/truenas/scripts/setup.sh`
from your clone. Requires Python 3.10+ and git.

## Configure

1. **Create an API key** in the TrueNAS web UI: click your user icon (top right) → *API Keys*
   → *Add*. Keys are linked to a user; a `FULL_ADMIN` user (root, truenas_admin, or any user
   with that role) can make changes, a `READONLY_ADMIN` user gives a read-only key.
2. **Export environment variables** in the shell you run Claude Code from:

   ```
   export TRUENAS_HOST=nas.example.lan          # hostname[:port], or a full wss:// URI
   export TRUENAS_API_KEY='1-abc...'
   export TRUENAS_SSH_HOST=root@nas.example.lan # optional; defaults to root@$TRUENAS_HOST
   ```

   Optional: `TRUENAS_USER` (the key's user, switches to `auth.login_ex`), `TRUENAS_SCHEME=ws`
   for an HTTP-only UI, `TRUENAS_VERIFY_SSL=0` for a self-signed certificate (Claude will not
   set this on its own), `TRUENAS_VENV` to relocate the virtualenv, `TRUENAS_DEBUG=1` for full
   server tracebacks.
3. **SSH** (optional but recommended for logs): put your public key on the NAS user
   (*Credentials → Users → edit → Authorized Keys*) and enable the SSH service.

Smoke test from a clone:

```
python3 skills/truenas/scripts/tn.py info
```

Offline tests (no NAS needed): `python -m unittest discover tests`. The end-to-end tests spin
up `tests/fake_middleware.py` and need `websockets` in the same virtualenv as the client
(`~/.cache/truenas-skill/venv/bin/pip install websockets`); they skip otherwise.

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

## Layout

```
.claude-plugin/plugin.json      plugin manifest
.claude-plugin/marketplace.json single-plugin marketplace
skills/truenas/SKILL.md         instructions Claude loads when the skill triggers
skills/truenas/scripts/tn.py    JSON-RPC helper: info | call | methods | jobs | query
skills/truenas/scripts/setup.sh installs truenas_api_client into a virtualenv
skills/truenas/references/      cheat sheets: api-basics, storage, sharing, apps, system
tests/                          offline unit tests (python -m unittest discover tests)
evals/evals.json                example prompts for skill evaluation
```

## Reaching a NAS on another VLAN

If the NAS web ports are firewalled from where you run Claude, `tn.py info` reports
`"cause": "timeout"`. Forward the port through a host you can SSH to and point the skill at
the tunnel:

```
ssh -fN -L 8443:<nas-ip>:443 <user>@<jump-host>
export TRUENAS_HOST=127.0.0.1:8443
export TRUENAS_SSH_HOST=root@<nas-ip>      # with a ProxyJump entry in ~/.ssh/config
```

The NAS certificate will not match `127.0.0.1`; `skills/truenas/references/api-basics.md`
lists the options, of which `TRUENAS_VERIFY_SSL=0` is the bluntest. Claude will report the
certificate error and wait for your decision rather than set it.

## Compatibility and verification status

Written for TrueNAS SCALE 25.04 (Fangtooth) and 25.10 (Goldeye). The skill asks the live
system for method schemas (`core.get_methods`) before unfamiliar calls, so minor API drift
between releases is handled at run time. TrueNAS CORE and 24.10 are not supported (different
API protocol).

What has actually been exercised so far:

- `tn.py` with client tag `TS-25.10.7` on Python 3.13 against `tests/fake_middleware.py`
  (login, queries, a job with progress events, validation and call errors). Python 3.14 is
  untested; the pinned client predates it.
- Static review of the scripts and references by a second session.
- Not yet: a run against a real TrueNAS. The method names and argument shapes in
  `references/` are from documentation and memory. The first live target is a 25.10.6 system;
  until that run lands, treat a reference example as a starting point and the live schema
  from `tn.py methods <name> --schema` as the truth.
