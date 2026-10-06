# Changelog

All notable changes to this plugin are documented in this file.

The format follows Keep a Changelog. Versions follow the `version` field in
`.claude-plugin/plugin.json`; the version must be bumped for installed users to
receive an update.

## 0.2.0 - 2026-10-06

### Changed

- `/proxmox:doctor` is prose: four read-only `pve-api.sh` GET calls (version,
  token permissions, nodes, cluster status) plus the optional SSH check that
  Claude interprets, instead of a script.
- `pve` skill description rewritten with trigger terms (pvesh, qm, pct, vzdump,
  pvesm, pveum, ha-manager, VMID, hypervisor node, homelab cluster) and an
  exclusion line (PBS, PMG, VMware, libvirt, Docker, Kubernetes, plain Debian).
- Action skills deduplicated and unnumbered: the safety contract, tooling
  contract, env table and the empty-storage pitfall live only in the `pve`
  skill; `vm`, `ct`, `snapshot`, `backup` and `status` point to it.
- `/proxmox:status` and `/proxmox:doctor` declare `allowed-tools` for
  `pve-api.sh GET` calls, so read-only checks run without a permission prompt
  (verified with `claude --plugin-dir` in headless mode).
- The `proxmox-operator` agent defers to the preloaded `pve` skill for the
  gated and free lists and runs the three-call connection check inline.
- `## Contents` sections in every reference over 100 lines.
- Env tables (README and `pve` skill) document `PVE_API_QUIET_TLS` and the
  corrected `PVE_HOST` note (scheme, port 8006, trailing `/` and `/api2/json`
  stripped, bracketed IPv6).
- Exit-code wording aligned across `--help`, the `pve` skill, README and
  `troubleshooting.md` (`pve-task.sh` 3 = usage, bad UPID or jq missing;
  `pve-ssh.sh` 1 = usage, no host or no ssh).
- Guard rule 12 matches `pvenode suspendall` in addition to `stopall` and
  `migrateall`.

### Added

- `PVE_DRY_RUN=1` for `pve-api.sh`: prints `{method, url, params}` as JSON and
  exits 0 without contacting the API.
- `skills/pve/evals/trigger-evals.json`: 20 trigger evals (10 should trigger,
  10 should not) for the `pve` skill description.
- Tests for the dry run, `pvenode suspendall` and the four doctor GET calls.
- Lint checks: no `argument-hint` on `doctor`, `doctor` at most 70 lines,
  `allowed-tools` only in the GET form, expected script set, `## Contents` in
  long references, trigger-evals shape, no `pve-doctor` mention outside this
  changelog.

### Removed

- `scripts/pve-doctor.sh`; its checks are now the `/proxmox:doctor` skill.

## 0.1.0 - 2026-10-06

### Added

- Main skill `pve` (`/proxmox:pve`): safety contract, environment setup,
  tooling contracts, workflow, quick reference and pitfalls for Proxmox VE 9.
- Reference guides under `skills/pve/references/`: API and CLI cheatsheets,
  permissions, cloud-init, storage, networking and SDN, cluster and HA,
  backups, troubleshooting, PVE 9 changes.
- Action skills (slash commands): `/proxmox:status`, `/proxmox:doctor`,
  `/proxmox:vm`, `/proxmox:ct`, `/proxmox:snapshot`, `/proxmox:backup`.
- Subagent `proxmox-operator` (`@agent-proxmox:proxmox-operator`) with the
  `CONFIRMED: <action> <target>` protocol for gated actions.
- PreToolUse guard hook (`hooks/hooks.json` + `scripts/guard.sh`) that forces a
  permission prompt for destructive or disruptive Bash commands.
- Scripts: `pve-api.sh` (REST calls with API token), `pve-task.sh` (UPID
  polling and task log), `pve-doctor.sh` (connection and capability check),
  `pve-ssh.sh` (SSH tier to nodes), `guard.sh` (hook).
- Tests: mock Proxmox API server, script tests, guard rule table, plugin lint,
  `claude plugin validate`.
- Qualitative evals for the `pve` skill in `skills/pve/evals/evals.json`.
- Marketplace manifest for `claude plugin install proxmox@jonasthim`.
