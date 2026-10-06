# Networking, SDN and firewall

Condensed from Proxmox 9.x sources (pve-network, pve-manager network API, pve-firewall,
pve-docs); items marked UNVERIFIED were not confirmed.

## How node networking changes work

- ifupdown2 is the default since PVE 7.0. API and GUI edits are written to
  `/etc/network/interfaces.new` (staged) and do nothing until applied.
- Apply = `PUT /nodes/{node}/network` (runs `ifreload -a`, returns a UPID, needs
  Sys.Modify on `/nodes/{node}`). This is gated: a wrong bridge or gateway cuts the node
  and every guest on it off the network. Put the revert path in the PLAN block.
- Revert staged edits = `DELETE /nodes/{node}/network` (gated as a DELETE, but it is the
  safe direction).
- Hand edits over SSH go to `/etc/network/interfaces`; apply them with `ifreload -a`
  (gated). Each node's file must end with `source /etc/network/interfaces.d/*` or SDN
  configuration is not loaded.

## Inspect

```
pve-api.sh GET /nodes/N/network
pve-api.sh GET /nodes/N/network type=bridge        # also any_bridge, any_local_bridge, include_sdn
pve-api.sh GET /nodes/N/network/vmbr0
pve-api.sh GET /cluster/resources type=sdn
```

## Create and edit interfaces (staged, free)

`POST /nodes/N/network` with `iface`, `type` (`bridge|bond|eth|alias|vlan|fabric|OVS*`) and
the keys that fit: `bridge_ports`, `bridge_vlan_aware`, `cidr`, `gateway`, `autostart`,
`mtu`, `slaves`, `bond_mode`, `vlan-id`, `vlan-raw-device`, `comments`.
`PUT /nodes/N/network/{iface}` edits; `DELETE /nodes/N/network/{iface}` removes (gated).

```
pve-api.sh POST /nodes/N/network iface=vmbr1 type=bridge bridge_ports=eno2 autostart=1 comments="lab"
pve-api.sh POST /nodes/N/network iface=bond0 type=bond slaves="eno1 eno2" bond_mode=active-backup
pve-api.sh POST /nodes/N/network iface=vmbr0.20 type=vlan vlan-id=20 vlan-raw-device=vmbr0 cidr=10.0.20.5/24
pve-api.sh PUT /nodes/N/network/vmbr0 bridge_vlan_aware=1
```

Value formats for `slaves` and `bond_mode` are UNVERIFIED (check `pvesh usage
/nodes/N/network -v --command create`). Then PLAN and, after confirmation:

```
pve-api.sh PUT /nodes/N/network | pve-task.sh -
```

VLAN-aware bridge in `/etc/network/interfaces`: `bridge-vlan-aware yes`,
`bridge-vids 2-4094`. Stable NIC names after hardware changes:
`pve-network-interface-pinning generate` over SSH.

## SDN

There is no `pvesdn` CLI; use the API (or `pvesh` with the same paths). Objects are
staged cluster-wide and become active only on apply.

- Zones: `GET|POST /cluster/sdn/zones` (`zone`, `type simple|vlan|qinq|vxlan|evpn|faucet`),
  `GET|PUT|DELETE /cluster/sdn/zones/{zone}`.
- VNets: `GET|POST /cluster/sdn/vnets` (`vnet`, `zone`, `tag`, `alias`, `vlanaware`,
  `isolate-ports`), `GET|PUT|DELETE /cluster/sdn/vnets/{vnet}`.
- Subnets: `GET|POST /cluster/sdn/vnets/{vnet}/subnets` (`subnet` CIDR, `type=subnet`,
  `gateway`, `snat`, `dhcp-range`, `dhcp-dns-server`, `dnszoneprefix`);
  `.../subnets/{id}` where the id is `<zone>-<ip>-<mask>`.
- Also under `/cluster/sdn`: `controllers`, `ipams`, `dns`, `fabrics` (new in 9: OpenFabric,
  OSPF; WireGuard fabric in 9.2), `prefix-lists`, `route-maps`, `lock`, `rollback`, `dry-run`.
- Apply: `PUT /cluster/sdn` (SDN.Allocate on `/sdn`; params `lock-token`, `release-lock`);
  gated. Rollback: `/cluster/sdn/rollback`; gated.

```
pve-api.sh POST /cluster/sdn/zones zone=lab type=simple
pve-api.sh POST /cluster/sdn/vnets vnet=labnet zone=lab
pve-api.sh POST /cluster/sdn/vnets/labnet/subnets subnet=10.50.0.0/24 type=subnet gateway=10.50.0.1 snat=1
pve-api.sh GET /cluster/sdn/vnets/labnet/subnets
# PLAN, confirm, then:
pve-api.sh PUT /cluster/sdn
```

Per-vnet ACL path for guests: `/sdn/zones/<zone>/<vnet>`; creating a guest on a bridge or
vnet needs SDN.Use there (PVESDNUser on `/sdn` is the recommended operator grant).

## Firewall

Prefixes: `/cluster/firewall` (options, rules, groups, ipset, aliases, macros, refs);
`/nodes/{node}/firewall` (options, rules, log);
`/nodes/{node}/qemu/{vmid}/firewall` and `/nodes/{node}/lxc/{vmid}/firewall`
(options, rules, aliases, ipset, log, refs).

Rules: `GET|POST <prefix>/rules`; `GET|PUT|DELETE <prefix>/rules/{pos}`. Create params:
`type in|out|forward|group`, `action ACCEPT|DROP|REJECT|<group>`, `enable`, `source`,
`dest`, `proto`, `dport`, `sport`, `iface`, `macro`, `icmp-type`, `pos`, `log`, `comment`,
`digest`.

```
pve-api.sh GET /nodes/N/qemu/V/firewall/options
pve-api.sh GET /nodes/N/qemu/V/firewall/rules
pve-api.sh POST /nodes/N/qemu/V/firewall/rules type=in action=ACCEPT proto=tcp dport=22 source=10.0.0.0/8 enable=1 comment="ssh from lan"
pve-api.sh DELETE /nodes/N/qemu/V/firewall/rules/0          # gated
```

Rule creation and edits are free; deleting rules is gated. Read `.../options` at cluster,
node and guest level to see whether the firewall is enabled there before judging a rule's
effect. A rule that blocks management traffic locks you out, so for cluster or node rules
treat enabling the firewall like a network apply and PLAN it.
