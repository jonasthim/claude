# Mock fixtures

Synthetic UniFi site served by `unifi.py` when `UNIFI_MOCK_DIR` points here. Shapes follow
the Network Integration API 10.x as closely as known; they are for exercising workflows,
not a schema reference. Collections are `<resource>.json`; `<resource>_<sub>.json` files are
keyed by item id (`_default` is the fallback). `cloud_*.json` back the Site Manager commands.

Story baked in: the Office Switch (USW-Flex-Mini) is OFFLINE, the Office AP rebooted ~50 min
ago with high retries and has a firmware update, `jonas-mbp` is on that AP, the hallway camera
is on Core Switch port 7, and the WAN had a 94 s outage at 02:00.
