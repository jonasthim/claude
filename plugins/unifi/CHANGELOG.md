# Changelog

All notable changes to this plugin are documented in this file.

The format follows Keep a Changelog. Versions follow the `version` field in
`.claude-plugin/plugin.json`; the version must be bumped for installed users to
receive an update.

## Unreleased

### Changed

- Moved into the `jonasthim/claude` marketplace repository under `plugins/unifi`. Install with
  `claude plugin marketplace add jonasthim/claude` and `claude plugin install unifi@jonasthim`.
  The old `claude-unifi-skill` marketplace is retired.
- Added the MIT LICENSE file.
- Added `tests/smoke.sh`, an offline run against the eval fixtures.

### Fixed

- `devices action`, `clients action` and `devices adopt` refuse to run without `--action`
  or `--macs` instead of sending `null` to the controller.
- `ssh_diag.sh` rejects an unknown section name instead of returning empty diagnostics.

## 0.2.0 - 2026-10-06

- Initial release.
