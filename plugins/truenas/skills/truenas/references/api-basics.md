# API basics: connection, auth, queries, jobs, errors

## Endpoint and versions

- JSON-RPC 2.0 over websocket at `wss://<host>/api/current` (25.04 and later). The legacy
  `/websocket` endpoint still exists for 24.10-era clients; do not use it.
- The REST API (`/api/v2.0/...`) is deprecated: using it raises a WARNING alert on the NAS
  that counts the calls and names the caller IPs, and it is removed in 26.04. Do not reach for
  `curl /api/v2.0` even for a quick read.
- `GET https://<host>/api/versions` (unauthenticated) lists supported API versions, e.g.
  `["v24.10", "v25.04.0", "v25.04.1", "v25.10.0"]`. `tn.py info` includes this.
- `tn.py` builds the URI from `TRUENAS_HOST`. Set `TRUENAS_SCHEME=ws` only for an HTTP-only UI.
- Per-method docs live at https://api.truenas.com/v<version>/ but the live schema from
  `core.get_methods` is authoritative for the box you are talking to.

## Transports

`tn.py` speaks to the same middleware two ways. `ws` is the primary transport and `ssh` the
fallback: an API key is scoped to one user, shows up in the audit log under that user, and can
be revoked in the UI on its own; `midclt` over SSH runs as root (or via sudo) and an SSH key
on a production NAS is a broader, less visible grant. Use `ssh` when the web port is
unreachable or the user prefers it, not as the default.

| | `ws` | `ssh` |
|---|---|---|
| Needs | web port (443) reachable, API key, trusted or accepted certificate | SSH login to the NAS (root, or a sudoer with NOPASSWD) |
| Env | `TRUENAS_HOST`, `TRUENAS_API_KEY`, optional `TRUENAS_USER`, `TRUENAS_VERIFY_SSL`, `TRUENAS_SCHEME` | `TRUENAS_SSH_HOST` (default `root@<bare TRUENAS_HOST>`), optional `TRUENAS_SSH_OPTS`, `TRUENAS_SSH_SUDO` |
| Per call | one websocket, one login, then as many calls as you like | one `ssh` process per call (a second or two each); fine for interactive use, slow for loops |
| Jobs | progress events with percent | `midclt call -j` prints description lines; no percent |
| Audit | calls are logged under the API key's user | calls run as root (or via sudo) on the box |
| Chosen when | `TRUENAS_API_KEY` is set | otherwise, or `TRUENAS_TRANSPORT=ssh` / `--transport ssh` |

Both produce the same JSON, the same error shape, and honour the same `--confirm` gate.

### A NAS on another VLAN

A common homelab layout puts the NAS on a server VLAN and firewalls its web ports (443, 80,
8080) from the admin workstation, while SSH is allowed, directly or through a jump host. `tn.py
info` over `ws` then fails with `"cause": "timeout"`. Two ways through; present both.

**Keep the API key and forward the web port** (preferred: same audit trail, same scoped key):

```
ssh -fN -L 8443:<nas-ip>:443 <user>@<jump-host>
export TRUENAS_HOST=127.0.0.1:8443
```

The factory iXsystems certificate has CN and SAN `localhost` only, so connect to the forward
as `localhost` (`TRUENAS_HOST=localhost:8443`): the name then matches and only the chain
fails (`"cause": "certificate"`, "self-signed certificate"). Check what the certificate
actually has before proposing anything:
`openssl s_client -connect 127.0.0.1:8443 -servername <nas-name> </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -ext subjectAltName`.
Options, in order of preference: a certificate from the user's own CA (TrueNAS: Credentials →
Certificates, then System → General → GUI SSL Certificate) with the NAS hostname in its SANs;
for a factory certificate, trust that exact certificate on this machine (`SSL_CERT_FILE`
pointing at a bundle that includes it, or the OS trust store); or `TRUENAS_VERIFY_SSL=0`. The last sends the API key
over an unverified TLS session inside an SSH tunnel, so the exposure is the jump-host-to-NAS
hop. State the trade-off and let the user choose.

**Fall back to the ssh transport** (works immediately if SSH to the NAS is already set up;
runs as root on the box):

```
export TRUENAS_SSH_HOST=root@<nas-ip>
export TRUENAS_SSH_OPTS="-J <user>@<jump-host>"     # only if SSH itself must hop
tn.py --transport ssh info
```

## Authentication

- API keys are user-linked (Settings → API Keys, or user menu → API Keys). The key string
  looks like `N-xxxxxxxx...`. The user it belongs to needs API access and, for changes,
  the right roles (a `FULL_ADMIN` user, or `root`/`truenas_admin`).
- `tn.py` logs in with `auth.login_with_api_key(key)`, or `auth.login_ex` with
  `API_KEY_PLAIN` when `TRUENAS_USER` is set. Both are the 25.x paths; SCRAM-based key auth
  arrived in 26.0.
- Read-only keys: TrueNAS 25.x assigns privileges per user, not per key. A user with a
  `READONLY_ADMIN` role gets a key that cannot change anything. Expect `EPERM` errors from
  write methods in that case and tell the user which role is missing.
- `auth.me` returns the current user and its privilege roles. Useful when a call fails with
  a permission error.

## Query filters and options

Every `*.query` method takes `[filters, options]`:

```
filters: [["name", "=", "tank/photos"], ["type", "!=", "VOLUME"]]
         operators: = != > >= < <= ~ (regex) in nin ^ (startswith) $ (endswith)
         OR groups: [["OR", [["a","=",1],["b","=",2]]]]
options: {"select": ["name", "used"], "order_by": ["-id"], "limit": 20, "offset": 0,
          "count": true, "get": true, "extra": {...}}
```

- `get: true` returns one object instead of a list (errors if none match).
- `count: true` returns an integer.
- `extra` is namespace-specific (e.g. `pool.dataset.query` accepts `{"flat": false}` to get a
  tree, `{"properties": [...]}` to limit ZFS properties, `{"retrieve_children": false}`).
- Nested fields use dots in filters and select: `used.parsed`, `progress.percent`.
- `tn.py query NAMESPACE --filter k=v --select a,b --limit N --extra '{...}'` builds this.

Dataset properties come back as `{"parsed": ..., "rawvalue": "...", "value": "...", "source": ...}`
objects; use `.parsed` for numbers.

## Jobs

- A job method returns an integer job id. `tn.py call ... --job` subscribes to `core.get_jobs`
  events, waits, and prints the result. Without `--job` you get the bare id.
- `core.get_jobs [filters] [options]` lists jobs. Fields: `id`, `method`, `arguments`,
  `state` (WAITING, RUNNING, SUCCESS, FAILED, ABORTED), `progress {percent, description}`,
  `result`, `error`, `exception`, `exc_info`, `time_started`, `time_finished`, `logs_path`.
- `core.job_abort id` cancels a running job (gated? no, but tell the user first).
- `core.job_wait id` blocks until a job finishes (itself a job; use `--job`).
- 25.10 "new-style jobs": any method may return a job; `tn.py methods` shows `"job": true`.

## Method discovery

```
tn.py methods pool.snapshot                          # substring match: names, first description line, flags
tn.py methods pool.snapshot.create --exact --schema  # this exact method, with accepts/returns schema
tn.py methods app --full                             # raw core.get_methods entries
```

`core.get_methods` is large (hundreds of entries). Always pass a prefix. Two things that
mislead: the default match is a substring, so `methods service.update` also returns
`alertservice.update` and "something came back" is not proof the method exists; and
descriptions contain phrases like "if `id` is not found", so grepping output for error-ish
words gives false negatives. Use `--exact` to prove one name exists (exit 1 and a list of
similar names if it does not). In the output, `job` comes from the server; `gated_by_tn` is
this tool's own `--confirm` list, not server data.

## Errors

`tn.py` prints errors as JSON on stderr:

```
{"error": "pool.dataset.create failed", "type": "ValidationErrors",
 "validation_errors": [{"attribute": "pool_dataset_create.name",
                        "message": "Dataset 'tank/photos' already exists"}]}
```

- `ValidationErrors`: wrong or missing arguments. The attribute names the field.
- `errno` values: `EPERM`/1 or `EACCES`/13 permission or role problem, `ENOENT`/2 object not
  found, `EEXIST`/17 already exists, `EBUSY`/16 dataset in use (shares, apps, open files),
  `ENOMETHOD`/201 method name wrong for this version (check `methods`).
- `"server_exception"` carries the last line of the middleware traceback. Set
  `TRUENAS_DEBUG=1` to include the full formatted traceback.
- Connection errors (exit 1 with a `hint`) mean host, port, TLS or firewall. Try SSH +
  `midclt call system.info` to tell "middleware down" from "network unreachable".

## midclt and the TrueNAS CLI over SSH

On the box itself:

```
midclt call pool.dataset.query '[["name","=","tank"]]'
midclt call -j pool.scrub.run tank              # -j waits on the job
cli -c "storage dataset query"                  # the interactive TrueNAS CLI (midcli)
```

The `ssh` transport is exactly `ssh <target> midclt call <method> <json args...>` with `-j`
for jobs and `sudo -n` prefixed when the SSH user is not root. `tn.py --transport ssh` is
preferable to typing that by hand because it JSON-quotes arguments, parses the result, applies
the destructive gate, and turns midclt's stderr into the same error JSON as the `ws` path.

## Useful read-only methods

| Method | Purpose |
|---|---|
| `system.info` | hostname, version, uptime, memory, load |
| `system.version`, `system.version_short` | version strings |
| `system.general.config`, `system.advanced.config` | UI port, timezone, kernel/console settings |
| `core.ping` | `"pong"` if the middleware answers |
| `core.get_services` | namespaces available |
| `auth.me`, `auth.sessions` | who you are, active sessions |
| `reporting.netdata_get_data` / `reporting.graphs` | metrics (prefer SSH `top`/`zpool iostat` for quick looks) |
