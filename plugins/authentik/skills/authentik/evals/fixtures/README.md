# Mock fixtures

A synthetic authentik server answered by `authentik.py` when `AUTHENTIK_MOCK_DIR` points here, and
over HTTP by `../mock_server.py`. Shapes follow the authentik 2026.8.3 schema; they exercise
workflows and are not a schema reference (an event's `context` in particular is free-form). Every
name is under `example.com`, every address is from a documentation range, and every "secret" is a
marker string that tests look for.

Collections are `<name>.json`; `me.json` and `version.json` back the connection check; `system.json`
is `/admin/system/` with echoed request headers, to exercise redaction;
`check_access.json` is keyed by `<slug>:<user pk>` and `outpost_health.json` by outpost UUID, each
with `_default` as the fallback. Writes are echoed back and never stored.

Story baked in:

- **Dashboards** has `policy_engine_mode: all` and two group bindings, `dashboards-users` and
  `dashboards-admins`. Bob is in the first group only, so authentik refuses him; Alice is in both.
  Someone added the admins group meaning "these may enter too" and got "and" instead of "or".
- Bob also failed his password twice before logging in, which is a second, unrelated trail in the
  event log. A plain binding denial leaves no event.
- **Files** is a proxy application whose provider is on no outpost, and it has no bindings, so
  every user may open it. There is a `configuration_error` event about it.
- **Directory** has one binding, and it is disabled.
- **Legacy portal** has no provider. Provider **Old cloud** has no application.
- The LDAP outpost runs 2026.5.1 against a 2026.8.3 server.
- Carol's account is inactive.
- The token belongs to `svc-automation`, a superuser service account.
