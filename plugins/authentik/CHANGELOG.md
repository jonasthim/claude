# Changelog

All notable changes to this plugin are documented in this file.

The format follows Keep a Changelog. Versions follow the `version` field in
`.claude-plugin/plugin.json`; the version must be bumped for installed users to
receive an update.

## 0.1.0 - 2026-10-06

### Added

- Skill `authentik`: first call, workflows, the binding and engine-mode model, change protocol
  and secret handling for the authentik REST API, with an API reference and playbooks condensed
  from the authentik 2026.8.3 schema, source and documentation.
- `authentik.py`, a standard-library CLI: applications (with a plain-language reading of their
  bindings and an access check per user), providers, groups, users, bindings, policies, flows,
  outposts, events, scope mappings, tokens, a health report and a raw call. Every write is
  refused without `--yes`; `--dry-run` prints the request; secrets are redacted by default; TLS
  is verified by default.
- Slash command `/authentik:doctor`: connection, token and visibility check.
- Fixtures for a synthetic server, a mock API server, and offline tests.
