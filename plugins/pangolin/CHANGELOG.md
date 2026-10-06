# Changelog

All notable changes to this plugin are documented in this file.

The format follows Keep a Changelog. Versions follow the `version` field in
`.claude-plugin/plugin.json`; the version must be bumped for installed users to
receive an update.

## 0.1.0 - 2026-10-06

### Added

- Skill `pangolin`: first call, workflows, change protocol and secret handling for the Pangolin
  Integration API, with an API reference and playbooks condensed from the Pangolin 1.24.0 source.
- `pangolin.py`, a standard-library CLI: sites, resources, targets, rules, domains, identity
  providers, roles, users, clients, a health report, an access probe and a raw call. Every write
  is refused without `--yes`; `--dry-run` prints the request; secrets are redacted by default;
  TLS is verified by default.
- Slash command `/pangolin:doctor`: connection, key and read-access check.
- Fixtures for a synthetic organization, a mock API server, and offline tests.
