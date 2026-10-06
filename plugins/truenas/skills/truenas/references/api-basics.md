# API basics: connection, auth, queries, jobs, errors

## Endpoint and versions

- JSON-RPC 2.0 over websocket at `wss://<host>/api/current` (25.04 and later). The legacy
  `/websocket` endpoint still exists for 24.10-era clients; do not use it.
- `GET https://<host>/api/versions` (unauthenticated) lists supported API versions, e.g.
  `["v24.10", "v25.04.0", "v25.04.1", "v25.10.0"]`. `tn.py info` includes this.
- `tn.py` builds the URI from `TRUENAS_HOST`. Set `TRUENAS_SCHEME=ws` only for an HTTP-only UI.
- Per-method docs live at https://api.truenas.com/v<version>/ but the live schema from
  `core.get_methods` is authoritative for the box you are talking to.

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
tn.py methods pool.snapshot              # names, first line of description, job/confirm flags
tn.py methods pool.snapshot.create --schema   # accepts/returns JSON schema
tn.py methods app --full                 # raw core.get_methods entries
```

`core.get_methods` is large (thousands of entries). Always pass a prefix.

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

`midclt` installed by `setup.sh` also works remotely:
`~/.cache/truenas-skill/venv/bin/midclt --uri wss://<host>/api/current -K <key> call system.info`
(25.10 client: `-K` takes the raw key; `--plain` is not needed against 25.x).

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
