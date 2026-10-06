# TrueNAS SCALE

Operates a TrueNAS SCALE 25.x system: pools, datasets, zvols, snapshots and scrubs; SMB, NFS and iSCSI shares
and ACLs; apps; alerts; services; system updates; replication and cloud sync. One standard-library Python
script (`tn.py`) talks to the middleware over its JSON-RPC websocket API with an API key, or over SSH by
running `midclt` on the NAS. Python 3.10+; no packages to install.

Full reference: [plugins/truenas/README.md](../plugins/truenas/README.md).

## Create an API key

In the TrueNAS web UI: your user icon (top right) → *API Keys* → *Add*. The key acts as its user: a
`READONLY_ADMIN` user gives a read-only key, a `FULL_ADMIN` user (root, truenas_admin, or any user with that
role) one that can make changes.

## Configure

| Variable | Required | Purpose |
|---|---|---|
| `TRUENAS_HOST` | yes | `hostname[:port]`, or a full `wss://` URI |
| `TRUENAS_API_KEY` | yes | The API key |
| `TRUENAS_USER` | no | The key's user (switches to `auth.login_ex`) |
| `TRUENAS_SCHEME` | no | `ws` for an HTTP-only web UI |
| `TRUENAS_VERIFY_SSL` | no | `0` accepts a self-signed certificate. Set it yourself; Claude never does |
| `TRUENAS_SSH_HOST` | no | `user@host` for the SSH transport and read-only shell diagnostics (`zpool status`, `docker logs`) |
| `TRUENAS_SSH_OPTS` | no | Extra `ssh` options, for example `-J me@jump.lan` |
| `TRUENAS_TRANSPORT` | no | `ws` or `ssh` to force a transport |
| `TRUENAS_DEBUG` | no | `1` includes server tracebacks in errors |
| `TRUENAS_SHOW_SECRETS` | no | `1` turns off redaction. Only when you need a credential shown |

The SSH transport runs `midclt` as root (or a user with passwordless sudo), a broader grant than an API key.
Prefer the API, through a port forward if needed (below).

## What to ask

Just talk about your NAS:

- "Is my NAS healthy? Anything I should worry about?"
- "Snapshot tank/photos, then make the photos SMB share read-only for guests."
- "Jellyfin is stuck deploying, figure out why and fix it."
- "Set up an NFS export of tank/media for 192.168.1.0/24, read-only."
- "Which snapshots on tank/backup are older than 60 days?"

## Safety

- `tn.py call` refuses methods that delete, roll back, export, wipe, lock, reboot, update the OS, change ACLs
  or permissions, stop apps or services, or change users, network and boot configuration, unless `--confirm`
  is passed. Claude adds it only after you agree to that specific operation.
- Reads, creates, property updates, snapshots, service restarts and app starts, redeploys and upgrades run
  without asking.
- Passwords, secrets, tokens, private keys, dataset keys and credential attributes come back as `[REDACTED]`.

See also the [safety model](safety.md).

## Troubleshooting

- **Web port blocked from where Claude runs, SSH works**: forward it, `ssh -fN -L 8443:<nas-ip>:443 <jump>`,
  and set `TRUENAS_HOST=127.0.0.1:8443`. Expect a certificate name mismatch; Claude reports it and waits for
  your decision instead of disabling verification.
- **Unsupported**: TrueNAS CORE and SCALE 24.10 (different API protocol).
