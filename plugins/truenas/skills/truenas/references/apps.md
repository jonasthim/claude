# Apps (Docker) on TrueNAS SCALE 25.x

Since 24.10, apps run on Docker Compose; there is no Kubernetes. Each app is a compose
project with containers named `ix-<app>-<service>-1`. App data lives under the apps dataset
(`<pool>/ix-apps`, mounted at `/mnt/.ix-apps`).

## Inspect

```
tn.py query app --select name,state,upgrade_available,latest_version,version,image_updates_available
tn.py call app.get_instance jellyfin                 # full record incl. active_workloads
tn.py call app.config jellyfin                       # current values (what the UI form holds)
tn.py call app.upgrade_summary jellyfin              # what an upgrade would change
tn.py call app.rollback_versions jellyfin
tn.py call app.used_ports                            # host ports in use by apps
tn.py call app.outdated_docker_images
tn.py call docker.status                             # STATUS: RUNNING / UNCONFIGURED / ...
tn.py call docker.config                             # apps pool, address pools, nvidia flag
```

`state` is one of `RUNNING`, `STOPPED`, `DEPLOYING`, `CRASHED`, `STOPPING`.
`active_workloads.container_details[]` has each container's `id`, `service_name`, `image`,
`state`, `port_config`, `volume_mounts`. Use the container `id` or the `ix-...` name with
docker over SSH.

## Logs and live state (SSH)

```
ssh ... docker ps -a --filter name=ix-jellyfin --format '{{.Names}}\t{{.Status}}\t{{.Image}}'
ssh ... docker logs --tail 300 --timestamps ix-jellyfin-jellyfin-1
ssh ... docker inspect ix-jellyfin-jellyfin-1 --format '{{json .State}}'
ssh ... docker stats --no-stream
ssh ... docker compose -p ix-jellyfin ps            # compose view of the app
ssh ... ls /mnt/.ix-apps/app_configs/jellyfin/versions/   # rendered compose per version
ssh ... ls /mnt/.ix-apps/app_mounts/jellyfin/              # ix volumes (config dirs)
```

The API also exposes logs as an event stream (`app.container_log_follow`), which `tn.py`
does not subscribe to; use docker over SSH for logs. Middleware-side app errors
(deploy failures, pull failures) are in `tn.py jobs --id <job>` and `journalctl -u middlewared`.

## Lifecycle (jobs)

Not gated:

```
tn.py call app.start jellyfin --job
tn.py call app.redeploy jellyfin --job               # recreate containers with same config; fixes most "stuck" apps
tn.py call app.upgrade jellyfin '{"app_version": "latest"}' --job
tn.py call app.pull_images jellyfin '{"redeploy": true}' --job   # refresh images, e.g. for :latest tags
tn.py call app.update jellyfin '{"values": {...}}' --job          # change config; values must be the full schema section you change
```

Gated (ask, then `--confirm`):

```
tn.py call app.stop jellyfin --job --confirm
tn.py call app.rollback jellyfin '{"app_version": "1.2.3", "rollback_snapshot": true}' --job --confirm
tn.py call app.delete jellyfin '{"remove_images": true, "remove_ix_volumes": false, "force_remove_ix_volumes": false}' --job --confirm
```

`remove_ix_volumes: true` deletes the app's config data; say that explicitly when asking.

Troubleshooting order for "app X is not working":
1. `app.get_instance` → `state` and container states.
2. `docker logs` on the crashing container; look at the last 50 lines first.
3. `tn.py jobs` filtered to recent `app.*` jobs for failed deploys and their error.
4. Common causes: host port already used (`app.used_ports`), host path dataset locked or
   missing (`pool.dataset.query`), image pull failure (DNS or registry rate limit), config
   change that needs `app.redeploy`, GPU/device passthrough after an OS update.
5. Fix: `app.redeploy` first; `app.update` to correct values; `app.upgrade` if the catalog
   version fixed it; `app.rollback` only with the user's consent.

## Installing from the catalog

```
tn.py call catalog.config                            # trains: stable, community, enterprise, test
tn.py call app.available '[["name", "~", "jelly"]]'
tn.py call app.latest '[["name", "=", "jellyfin"]]'   # includes schema (questions) under app_metadata? check --schema
tn.py call catalog.sync --job                         # refresh catalog
```

Create (job, not gated). `values` follows the app's question schema; get it from
`app.available`/`app.latest` (`schema.questions`) or from an existing install's `app.config`:

```
tn.py call app.create '{"app_name": "jellyfin", "catalog_app": "jellyfin", "train": "stable",
  "version": "1.2.3", "values": {"jellyfin": {...}, "network": {"web_port": 8096},
  "storage": {"config": {"type": "ix_volume", "ix_volume_config": {"dataset_name": "config"}},
              "media": {"type": "host_path", "host_path_config": {"path": "/mnt/tank/media"}}}}}' --job
```

Omit `version` to install the latest in the train. Host-path storage needs the dataset to
exist and, for write access, the app's `run_as` uid/gid to have permission (apps default to
uid 568 `apps`). This is the most common post-install failure.

## Custom apps (compose)

```
tn.py call app.create '{"app_name": "whoami", "custom_app": true,
  "custom_compose_config": {"services": {"whoami": {"image": "traefik/whoami", "ports": ["8081:80"]}}}}' --job
tn.py call app.update whoami '{"custom_compose_config_string": "services:\n  whoami:\n    image: traefik/whoami\n    ports:\n      - 8082:80\n"}' --job
tn.py call app.convert_to_custom jellyfin --job --confirm   # catalog app -> editable compose (one way)
```

## Docker service settings

```
tn.py call docker.config
tn.py call docker.update '{"pool": "tank"}' --job --confirm          # choose/move apps pool (gated)
tn.py call docker.update '{"nvidia": true}' --job --confirm
tn.py call app.gpu_choices
tn.py call app.ip_choices
```

Moving the apps pool stops every app; do it only when the user explicitly wants that.
