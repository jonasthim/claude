# Changelog

All notable changes to this plugin are documented in this file.

The format follows Keep a Changelog. Versions follow the `version` field in
`.claude-plugin/plugin.json`; the version must be bumped for installed users to
receive an update.

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
