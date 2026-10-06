# Conventions

Every plugin in this marketplace follows the same rules, so that switching from one to another holds
no surprises for you or for Claude.

## Scope

- One plugin operates one product and is named after it (`truenas`, `proxmox`, `unifi`), through
  that product's own API. No category plugins ("observability", "homelab") that span products.
- A plugin works for anyone who runs the product. Nothing from one site goes into a skill, a
  reference, a fixture or an eval: no real hostnames, addresses, VLANs, ids, naming schemes or
  topology assumptions. Examples use `example.com` and the documentation address ranges.
- A workflow that strings several products together for one site belongs in that site's own
  repository as a project skill.

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
  `UNIFI_*`, `PANGOLIN_*`, `AUTHENTIK_*`); a mod uses `userConfig` instead.
- A scoped API key or token is the primary path; SSH is a fallback or a diagnostics tier.
- Claude never asks for, prints or stores a secret, and never turns TLS verification off on its own.

## Safety

- Reads run freely. Every destructive or disruptive action (delete, stop, rollback, network or
  firewall changes that can cut access) goes plan → confirm → apply → verify in the skill, **and**
  a code-level gate refuses it without an explicit flag or a guard hook asks first.
- Which lower-risk writes run without a confirmation (creates, starts, snapshots, ordinary
  updates) is each plugin's call and is written down in its skill: TrueNAS and Proxmox run them
  directly; UniFi, Pangolin and authentik confirm every write.
- A skill's `allowed-tools` pre-approves read commands only, so a write also passes the session's
  own permission mode. Skills spell out the full command for each call, since a shell variable
  does not survive between calls and would not match the pre-approved form. The pre-approval was
  seen to apply when the user starts the skill as a slash command; in a non-interactive session
  where Claude loaded the skill by itself, the same commands still needed approval.
- Output redacts secrets by default; showing them needs an explicit flag.
- Helper CLIs print JSON on stdout, a classified hint on stderr (auth, timeout, certificate, DNS),
  and use documented exit codes. A new CLI verifies TLS by default and does not follow redirects,
  so a credential is never sent to another address.
- API facts in skills and references name the version they come from. What was checked against a
  live system says so; what was not confirmed is marked UNVERIFIED.

## Known divergences (follow-ups)

- TLS: every plugin verifies certificates by default except UniFi (`UNIFI_VERIFY_TLS=1` turns it on).
- Gate flags differ: `--confirm` (truenas), `--yes` and `--dry-run` (unifi, pangolin, authentik), a
  PreToolUse guard hook (proxmox).
- The unifi skill pre-approves every `unifi.py` call, writes included; pangolin and authentik
  pre-approve reads only.
- Only proxmox lints its own manifest and skills; unifi has an offline smoke test against its fixtures but no unit tests.
- pangolin and authentik are written from source and documentation and have not been run against a
  live server yet; their READMEs say so.
- `pangolin.py` and `authentik.py` share a block of code (output, redaction, HTTP, the write gate)
  that is copied into both; a change to one copy belongs in the other.
