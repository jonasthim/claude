# Mock fixtures

Synthetic UniFi site served by `unifi.py` when `UNIFI_MOCK_DIR` points here. Shapes follow
the Network Integration API 10.x as closely as known; they are for exercising workflows,
not a schema reference. Collections are `<resource>.json`; `<resource>_<sub>.json` files are
keyed by item id (`_default` is the fallback). `cloud_*.json` back the Site Manager commands.

Story baked in: the Office Switch (USW-Flex-Mini) is OFFLINE, the Office AP rebooted ~50 min
ago with high retries and has a firmware update, `jonas-mbp` is on that AP, the hallway camera
is on Core Switch port 7 (known only to the user; the API deliberately does not expose it), and the WAN had a 94 s outage at 02:00.

Where fixtures are richer than a live 10.6 console: device list items here carry `uplink`, wifi
list items carry `security`/`networkId`, and WANs carry `enabled`/`type`. Live list endpoints
return summaries and `--table` hides the empty columns, so flows built on the fixtures still work.
