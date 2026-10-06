# Conventions

Every plugin in this marketplace follows the same rules, so that switching from one to another holds
no surprises for you or for Claude.

## Layout

- One plugin per folder under `plugins/<name>/`, with `.claude-plugin/plugin.json`, a `README.md`, a
  `LICENSE` and a `CHANGELOG.md`. Each plugin gets an entry in the root `.claude-plugin/marketplace.json`
  with `source: "./plugins/<name>"` and no `version`.
- `plugin.json`'s `version` is the only version. Bump it, and add a CHANGELOG entry (Keep a Changelog
  format), for any change installed users should receive.
- A plugin is installed as a copy of its own folder and reaches files only through
  `${CLAUDE_PLUGIN_ROOT}`, so plugins never import from one another or from the repository root.
  Shared code is copied into each plugin.
- Skills keep cheat sheets in `references/` and example prompts in `evals/`.

## Credentials

- Configuration comes from environment variables with the product's prefix (`TRUENAS_*`, `PVE_*`,
  `UNIFI_*`); a mod uses `userConfig` instead.
- A scoped API key or token is the primary path; SSH is a fallback or a diagnostics tier.
- Claude never asks for, prints or stores a secret, and never turns TLS verification off on its own.

## Safety

- Reads run freely. Every change goes plan → confirm → apply → verify in the skill, **and** a
  code-level gate refuses destructive calls without an explicit flag or a guard hook asks first.
- Output redacts secrets by default; showing them needs an explicit flag.
- Helper CLIs print JSON on stdout, a classified hint on stderr (auth, timeout, certificate, DNS),
  and use documented exit codes.

## Known divergences (follow-ups)

- TLS: TrueNAS and Proxmox verify certificates by default; UniFi does not (`UNIFI_VERIFY_TLS=1`
  turns it on).
- Gate flags differ: `--confirm` (truenas), `--yes` and `--dry-run` (unifi), a PreToolUse guard hook
  (proxmox).
- Only proxmox lints its own manifest and skills; unifi has an offline smoke test against its fixtures but no unit tests.
