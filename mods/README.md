# Mods

A mod is a Claude Code plugin made of function hooks: a TypeScript module the engine loads in place,
which can draw a pane, a band above the prompt or a status line entry, show toasts, register slash
commands, and intercept tool calls. Nothing ships here yet; this file holds the conventions for when
one does.

## Where a mod goes

A mod is a plugin like the others: `plugins/<mod>/`, with an entry in the root
`.claude-plugin/marketplace.json`. This folder holds documentation only, so the marketplace stays the
one index.

```
plugins/<mod>/
  .claude-plugin/plugin.json     { "name": "<mod>", "version": "0.1.0", "description": "..." }
  hooks/hooks.json               { "modules": ["./register.tsx"] }
  hooks/register.tsx             export const register: Register = (on, options) => { ... }
  types/index.d.ts               only if the mod keeps values in $.state
  hooks/*.test.ts                behaviour tests
```

- Settings and credentials are `userConfig` fields in `plugin.json` and reach the module as `options`,
  never environment variables or constants in the code.
- Check with `claude plugin validate plugins/<mod>` and `claude plugin test plugins/<mod>`. Develop
  with `claude --plugin-dir plugins/<mod>`.
- Install like any other plugin: `/plugin install <mod>@jonasthim`.

## Candidates

- **Homelab guard**: one `tool.call` hook that gates destructive `tn.py`, `pve-api.sh` and `unifi.py`
  calls the same way and redacts secrets in their output, replacing the per-plugin differences. The
  proxmox `guard.sh` command hook stays for clients without mod support.
- **Homelab health**: a band or status line entry showing pool health, cluster quorum and WAN state,
  refreshed on a timer.
