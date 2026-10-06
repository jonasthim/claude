# Mods

A mod is a Claude Code plugin made of function hooks: a TypeScript module that can intercept tool calls,
decide permissions, and draw a status line entry, a band above the prompt or a pane. None ship in this
marketplace yet.

**[docs/mods.md](../docs/mods.md)** is the guide: the hook model, the layout, a worked example
([`docs/examples/homelab-guard`](../docs/examples/homelab-guard)) with tests, and how to publish.

## Conventions

- A mod lives in `plugins/<mod>/` like any other plugin and has an entry in the root
  `.claude-plugin/marketplace.json`. This folder holds documentation only.
- Settings and credentials are `userConfig` fields in `plugin.json`, never environment variables or
  constants in the code.
- It passes `claude plugin validate plugins/<mod>` and `claude plugin test plugins/<mod>`, with at least one
  `*.test.ts` for the behaviour it adds.
- A guard never loosens a decision from beneath it and fails safe (a `.catch` that refuses or asks).
