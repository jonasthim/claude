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

  test('asks when the command is split across lines', async ($, on) => {
    on('tool.check', () => ({ decision: 'allow' }))
    expect((await $.tool.check(check('python3 tn.py call pool.dataset.delete \\\n  \'["tank/x"]\' --confirm'))).decision).toBe('ask')
    expect((await $.tool.check(check('python3 unifi.py wifi delete w1 \\\n  --yes'))).decision).toBe('ask')
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
