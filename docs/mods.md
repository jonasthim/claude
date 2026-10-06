# Building a mod

A mod is a Claude Code plugin made of **function hooks**: a TypeScript module the engine loads into a session
and calls at its events. Where a skill tells Claude how to do something, a mod changes what the session itself
does: it can intercept or answer tool calls, decide permissions, show a status line entry, a band above the
prompt, a pane or a toast, add a slash command, or change the system prompt.

This page builds one end to end. The finished example is in
[`docs/examples/homelab-guard`](https://github.com/jonasthim/claude/tree/main/docs/examples/homelab-guard),
and CI validates and tests it.

> Function hooks are an early-access Claude Code API and may change between releases. The `claude-code`
> types that each Claude Code build writes beside a loaded mod are the reference.

## The model in one paragraph

A module exports `register(on, options)`. `on(event, matcher?, hook)` adds a hook, and every hook has the
shape `($, e, next)`:

- `$` is the engine: `$.ui`, `$.tool`, `$.fs`, `$.clock` and the rest.
- `e` is the event's input.
- `next(e)` runs everything beneath (other plugins, then the engine's own behaviour) and resolves to the result.

A hook can return `next(e)` to pass, call `next({ ...e, x })` to rewrite, or return its own answer.
`options` holds the values of the `userConfig` fields declared in `plugin.json`.

## Layout

A mod in this repository lives beside the other plugins and is listed in the root marketplace:

```
plugins/<mod>/
  .claude-plugin/plugin.json   name, version, description (and userConfig for settings)
  hooks/hooks.json             { "modules": ["./register.ts"] }
  hooks/register.ts            the hooks module
  hooks/*.test.ts              tests, run by `claude plugin test`
  types/index.d.ts             only if the mod keeps values in $.state
```

Settings and credentials go in `userConfig` fields, never in the code or environment variables, so users set
them in the config menu when they install the mod.

## Worked example: homelab-guard

The three plugins already gate destructive calls in their own code. This mod adds one shared extra check on
top: whenever Claude is about to run a command that carries a plugin's "the user agreed" flag
(`tn.py ... --confirm`, `unifi.py ... --yes`) or an HTTP DELETE against Proxmox, the session asks you first,
even if a permission rule would have allowed the command.

It hooks `tool.check`, the event where the engine decides whether a tool call may run. `next(e)` gives the
verdict from your rules and mode, and the hook may turn an `allow` into an `ask`:

### 1. Manifest: `.claude-plugin/plugin.json`

```json
{
  "name": "homelab-guard",
  "version": "0.1.0",
  "author": { "name": "Jonas Thim" },
  "description": "Asks before any confirmed destructive TrueNAS, Proxmox or UniFi call runs."
}
```

### 2. Hook list: `hooks/hooks.json`

```json
{ "modules": ["./register.ts"] }
```

### 3. The module: `hooks/register.ts`

```ts
import type { Register } from 'claude-code'

// Commands that carry a plugin's own "I have confirmation" flag, or an HTTP DELETE.
// Each entry: a pattern over the Bash command, then the reason the dialog shows.
const GATED: readonly [RegExp, string][] = [
  [/\btn\.py\b.*\s--confirm\b/, 'a gated TrueNAS call (tn.py --confirm)'],
  [/\bunifi\.py\b.*\s--yes\b/, 'a UniFi write (unifi.py --yes)'],
  [/\bpve-api\.sh\s+delete\b/i, 'an HTTP DELETE against Proxmox (pve-api.sh DELETE)'],
]

export function gatedReason(command: string): string | undefined {
  for (const [pattern, reason] of GATED) {
    if (pattern.test(command)) return reason
  }
  return undefined
}

export const register: Register = on => {
  on('tool.check', { tool: 'Bash' }, async ($, e, next) => {
    const verdict = await next(e)
    const command = (e.input as { command?: unknown }).command
    const reason = typeof command === 'string' ? gatedReason(command) : undefined
    // Never loosen a deny; turn an allow into an ask for gated commands.
    if (reason === undefined || verdict.decision === 'deny') return verdict
    return { decision: 'ask', reason: `${$.plugin.name}: ${reason}. Confirm it is what you agreed to.` }
  }).catch(() => ({ decision: 'ask', reason: 'homelab-guard failed; confirm this command yourself.' }))
}
```

Points worth copying:

- **It never loosens.** A `deny` from beneath is returned untouched; only an `allow` becomes an `ask`.
- **It fails safe.** A hook that throws is skipped, so a guard would fail open. The `.catch` answers `ask`
  in its place.
- **The matcher** `{ tool: 'Bash' }` keeps the hook off every other tool.

### 4. Tests: `hooks/guard.test.ts`

In a test, `on` registers hooks beneath the plugin, standing in for the engine. Here they answer `allow` or
`deny`, and `$.tool.check` asks for a verdict without running anything:

```ts
import { describe, expect, test } from 'claude-code/testing'

const check = (command: string) => ({ tool: 'Bash', input: { command } })

describe('homelab-guard', () => {
  test('asks before a confirmed TrueNAS call', async ($, on) => {
    on('tool.check', () => ({ decision: 'allow' }))
    const verdict = await $.tool.check(check('python3 tn.py call pool.dataset.delete \'["tank/x"]\' --confirm'))
    expect(verdict.decision).toBe('ask')
  })

  test('asks before a UniFi write and a lowercase Proxmox delete', async ($, on) => {
    on('tool.check', () => ({ decision: 'allow' }))
    expect((await $.tool.check(check('python3 unifi.py devices restart d1 --yes'))).decision).toBe('ask')
    expect((await $.tool.check(check('pve-api.sh delete /nodes/pve1/qemu/100'))).decision).toBe('ask')
  })

  test('leaves reads and dry runs alone', async ($, on) => {
    on('tool.check', () => ({ decision: 'allow' }))
    expect((await $.tool.check(check('python3 tn.py call pool.query'))).decision).toBe('allow')
    expect((await $.tool.check(check('python3 unifi.py devices restart d1 --dry-run'))).decision).toBe('allow')
  })

  test('never loosens a deny', async ($, on) => {
    on('tool.check', () => ({ decision: 'deny', reason: 'blocked by a rule' }))
    expect((await $.tool.check(check('python3 unifi.py wifi delete w1 --yes'))).decision).toBe('deny')
  })
})
```

## Develop and check

```
claude plugin validate plugins/<mod>     # manifest and module, as the engine will read them
claude plugin test plugins/<mod>         # runs hooks/*.test.ts against the engine
claude --plugin-dir plugins/<mod>        # load it into a session for one run
```

Type-checking: once the engine has loaded the mod it writes `.claude-plugin/types/` and a `tsconfig.json`
beside it, and `tsc -p plugins/<mod>` checks the module. Claude Code's plugin-authoring skill can also build a mod with
you in a session, hot-reloading it as you edit, before you copy it here.

## Publish

1. Move the mod into `plugins/<mod>/` and add an entry to `.claude-plugin/marketplace.json`:

   ```json
   { "name": "<mod>", "source": "./plugins/<mod>", "description": "...", "category": "...", "keywords": [] }
   ```

2. Add a `README.md`, `LICENSE` and `CHANGELOG.md` like the other plugins ([CONTRIBUTING.md](../CONTRIBUTING.md)).
3. Install it from a terminal session: `/plugin install <mod>@jonasthim`.

## Limits worth knowing

- A mod's `ask` goes to the session's permission mode. In the default mode that is the dialog. In auto mode
  the classifier decides, so a mod like this one adds to the plugins' own gates and does not replace them.
  The Proxmox guard hook is a classic PreToolUse hook and always shows the prompt.
- The module runs in its own environment, with no DOM and no Node: everything outside goes through `$`.

## More ideas

- **Homelab health band**: a band above the prompt with pool health, cluster quorum and WAN state, refreshed
  on a timer (`$.clock`) by running the plugins' read-only commands.
- **Status line**: `$.ui.status()` showing which homelab credentials the session has loaded, without their values.
