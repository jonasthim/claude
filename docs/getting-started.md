# Getting started

## 1. Install

From a terminal:

```
claude plugin marketplace add jonasthim/claude
claude plugin install truenas@jonasthim
claude plugin install proxmox@jonasthim
claude plugin install unifi@jonasthim
claude plugin install pangolin@jonasthim
claude plugin install authentik@jonasthim
```

Or inside a Claude Code session: `/plugin marketplace add jonasthim/claude`, then
`/plugin install <plugin>@jonasthim`. Install only the plugins for systems you run.

If the repository is private, the marketplace add needs GitHub access that can read it (`gh auth login`, a
credential helper or an SSH key). Otherwise clone it and add the local path:

```
gh repo clone jonasthim/claude ~/src/claude
claude plugin marketplace add ~/src/claude
```

A plugin installed during a session becomes active in the next session (or after `/reload-plugins` where your
version has it).

## 2. Configure

Every plugin reads its connection settings from environment variables in the shell that starts Claude Code.
Keep them in a file only you can read and source it, so secrets never go into a prompt or a committed file:

```
# ~/.config/homelab.env  (chmod 600)
export TRUENAS_HOST=nas.example.lan
export TRUENAS_API_KEY='1-...'

export PVE_HOST=pve1.example.lan
export PVE_TOKEN_ID='claude@pve!ro'
export PVE_TOKEN_SECRET='...'
export PVE_CA_CERT=~/.config/pve-root-ca.pem

export UNIFI_HOST=192.168.1.1
export UNIFI_API_KEY='...'

export PANGOLIN_HOST=https://api.example.com
export PANGOLIN_API_KEY='...'
export PANGOLIN_ORG=my-org

export AUTHENTIK_HOST=https://auth.example.com
export AUTHENTIK_TOKEN='...'
```

```
source ~/.config/homelab.env && claude
```

Start with read-only credentials and widen them when you need Claude to make changes. Where to create each
key, and every optional variable, is on the plugin pages: [TrueNAS](truenas.md), [Proxmox VE](proxmox.md),
[UniFi](unifi.md), [Pangolin](pangolin.md), [authentik](authentik.md).

## 3. First checks

| Plugin | Ask or run | You should see |
|---|---|---|
| truenas | "Is my NAS healthy?" | Pool, alert and capacity summary |
| proxmox | `/proxmox:doctor` | A `check / result / detail` table and what the token can do per ACL path |
| unifi | "check my unifi setup" | The application version and site, or what is missing |
| pangolin | `/pangolin:doctor` | A `check / result / detail` table: API health, organization, and which reads the key allows |
| authentik | `/authentik:doctor` | A `check / result / detail` table: who the token is, the server version, and what it can see |

## 4. Update and uninstall

```
claude plugin marketplace update jonasthim
claude plugin update <plugin>@jonasthim
claude plugin uninstall <plugin>@jonasthim
```

Moving from the old single-plugin repositories (`claude-truenas-skill`, `claude-proxmox-skill`,
`claude-unifi-skill`)? Uninstall the plugins installed from those marketplaces, remove the marketplaces, and
install from `jonasthim` as above.
