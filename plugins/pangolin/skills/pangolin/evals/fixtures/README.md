# Mock fixtures

A synthetic Pangolin organization (`acme`) served by `pangolin.py` when `PANGOLIN_MOCK_DIR` points
here, and over HTTP by `../mock_server.py`. Shapes follow the Integration API of Pangolin 1.24.0 as
read from its source; they exercise workflows and are not a schema reference. Every name is under
`example.com` and every address is from a documentation range.

Collections are `<name>.json` (the list the API returns under that key); `org.json` is the
organization; `resource_<sub>.json` files are keyed by resource id, with `_default` as the fallback.
Writes are echoed back and never stored.

Story baked in:

- Site **Lab** is offline. **Status page** has its only target there and is `unhealthy`.
- **Dashboards** has two targets: one healthy on HQ, one unhealthy on Lab. That target's address is
  `198.51.100.141` but its health check still probes `198.51.100.140`: the backend moved and only
  the target was updated. With Lab offline there are two causes to tell apart.
- **Files** is enabled with no SSO, password, pincode, header auth or whitelist, and its target has
  no health check. It also has a `DROP /admin/*` rule that does nothing, because `applyRules` is false.
- **Wiki** is healthy and behind SSO; its two rules are inactive for the same reason.
- **Old blog** is disabled. **Git SSH** is a raw TCP resource on port 2222.
- Domain `example.org` failed verification.
- The Status page target carries an `Authorization` health-check header, to exercise redaction.
