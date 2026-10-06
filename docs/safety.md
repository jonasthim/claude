# Safety model

All plugins follow the same rules, written down in [CONTRIBUTING.md](https://github.com/jonasthim/claude/blob/main/CONTRIBUTING.md).

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

| | TrueNAS | Proxmox VE | UniFi | Pangolin | authentik |
|---|---|---|---|---|---|
| Gate | `tn.py` refuses gated methods without `--confirm` | PreToolUse guard hook asks on every gated command, even in auto mode | `unifi.py` refuses every write without `--yes` | `pangolin.py` refuses every write without `--yes` | `authentik.py` refuses every write without `--yes` |
| Preview | | `PVE_DRY_RUN=1` | `--dry-run` | `--dry-run` | `--dry-run` |
| Runs without asking | Reads, creates, property updates, snapshots, service restarts, app starts and upgrades | Reads, start/resume, create, clone, config without `delete`, snapshot create, backup runs, staged network objects | Reads only | Reads only | Reads only |
| Pre-approved in the session | | `GET` calls of `/proxmox:status` and `/proxmox:doctor` | Every `unifi.py` call | Read commands only, when started from the slash command; writes also go through your permission mode | Read commands only, when started from the slash command; writes and `raw` also go through your permission mode |
| Redaction | On; `--show-secrets` to turn off | Token secret never printed; dry runs mask passwords | On; `--show-secrets` to turn off | On; `--show-secrets` to turn off | On; `--show-secrets` to turn off |
| TLS verification | On; `TRUENAS_VERIFY_SSL=0` to turn off | On; `PVE_CA_CERT` for the cluster CA, `PVE_INSECURE=1` as a last resort | Off by default; `UNIFI_VERIFY_TLS=1` to turn on | On; `PANGOLIN_CA_CERT` for a private CA, `PANGOLIN_VERIFY_TLS=0` to turn off | On; `AUTHENTIK_CA_CERT` for a private CA, `AUTHENTIK_VERIFY_TLS=0` to turn off |
| Read-only credential | `READONLY_ADMIN` user's API key | `PVEAuditor` on `/` | Read-only admin's API key | Key with only list and get permissions | Service account in the group `authentik Read-only` |

Claude never turns TLS verification off or shows secrets on its own; those are your choices.

Creating credentials is your step: make API keys and tokens in the web UI or your own shell, since a new
secret is printed once and anything Claude runs lands in the transcript.

Pangolin and authentik decide who reaches your applications, so their plans also say what a change does to
protection: turning off a resource's SSO, deleting an application's last binding or switching its policy
engine mode can open it to everyone. The authentik plugin does not create tokens, set passwords, create
recovery links or impersonate users.
