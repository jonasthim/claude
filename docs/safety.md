# Safety model

All three plugins follow the same rules, written down in [CONTRIBUTING.md](../CONTRIBUTING.md).

1. **Reads run freely.** Status, listings, logs and health checks never ask.
2. **Destructive or disruptive actions need your yes.** Claude first shows what it will do, to what, the
   effect and how to undo it, and waits for your answer in a later message.
3. **A code-level gate backs that up**, so a model mistake alone cannot run the action.
4. **Secrets stay out of the conversation.** Claude never asks for a password or key in chat, never prints
   one, and redacts credentials in tool output by default. Credentials come from environment variables you
   set.
5. **Least privilege does the rest.** A read-only key or token cannot do damage, whatever the model decides.
   Start there.

## Per plugin

| | TrueNAS | Proxmox VE | UniFi |
|---|---|---|---|
| Gate | `tn.py` refuses gated methods without `--confirm` | PreToolUse guard hook asks on every gated command, even in auto mode | `unifi.py` refuses every write without `--yes` |
| Preview | | `PVE_DRY_RUN=1` | `--dry-run` |
| Runs without asking | Reads, creates, property updates, snapshots, service restarts, app starts and upgrades | Reads, start/resume, create, clone, config without `delete`, snapshot create, backup runs, staged network objects | Reads only |
| Redaction | On; `--show-secrets` to turn off | Token secret never printed; dry runs mask passwords | On; `--show-secrets` to turn off |
| TLS verification | On; `TRUENAS_VERIFY_SSL=0` to turn off | On; `PVE_CA_CERT` for the cluster CA, `PVE_INSECURE=1` as a last resort | Off by default; `UNIFI_VERIFY_TLS=1` to turn on |
| Read-only credential | `READONLY_ADMIN` user's API key | `PVEAuditor` on `/` | Read-only admin's API key |

Claude never turns TLS verification off or shows secrets on its own; those are your choices.

Creating credentials is your step: make API keys and tokens in the web UI or your own shell, since a new
secret is printed once and anything Claude runs lands in the transcript.
