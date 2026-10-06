import type { Register } from 'claude-code'

// Commands that carry a plugin's own "I have confirmation" flag, or an HTTP DELETE.
// Each entry: a pattern over the Bash command, then the reason the dialog shows.
// The `s` flag lets `.` cross newlines, so a command split with `\` is matched too.
const GATED: readonly [RegExp, string][] = [
  [/\btn\.py\b.*\s--confirm\b/s, 'a gated TrueNAS call (tn.py --confirm)'],
  [/\bunifi\.py\b.*\s--yes\b/s, 'a UniFi write (unifi.py --yes)'],
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
