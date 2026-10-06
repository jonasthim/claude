#!/usr/bin/env bash
# ssh_diag.sh - read-only diagnostics bundle from a UniFi OS console or device.
#
# Usage: ssh_diag.sh [user@]host [section ...]
#   sections: system net logs all (default: all)
#
# Runs one SSH session, never changes anything on the device. Requires key-based
# SSH (UniFi OS: Settings -> Control Plane -> Console -> SSH, add your public key).
set -euo pipefail

target="${1:-}"
[ -z "$target" ] && { echo "usage: $0 [user@]host [system|net|logs|all]" >&2; exit 1; }
shift
sections="${*:-all}"
case "$target" in *@*) ;; *) target="root@$target" ;; esac

want() { [[ " $sections " == *" all "* || " $sections " == *" $1 "* ]]; }

remote_script=""
if want system; then
remote_script+='
echo "### system"
(ubnt-device-info summary 2>/dev/null || info 2>/dev/null || cat /etc/board.info 2>/dev/null) | head -40
echo; echo "--- uptime / load"; uptime; cat /proc/loadavg 2>/dev/null
echo; echo "--- memory (MB)"; free -m 2>/dev/null || cat /proc/meminfo | head -3
echo; echo "--- disk"; df -h 2>/dev/null | grep -Ev "tmpfs|overlay" | head -15
echo; echo "--- temperatures"; ubnt-systool cputemp 2>/dev/null || true
echo; echo "--- unifi services"; (systemctl --no-pager --type=service 2>/dev/null | grep -Ei "unifi|ubios|ulp" || ps | grep -Ei "unifi|mcad|hostapd" | grep -v grep) | head -20
'
fi
if want net; then
remote_script+='
echo; echo "### network"
echo "--- interfaces"; ip -br addr 2>/dev/null || ifconfig | grep -E "^[a-z]|inet "
echo; echo "--- routes"; ip route 2>/dev/null | head -30
echo; echo "--- dns"; cat /etc/resolv.conf 2>/dev/null | grep -v "^#"
echo; echo "--- wan reachability"; ping -c 3 -W 2 1.1.1.1 2>&1 | tail -2
echo; echo "--- conntrack"; cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null; cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null
echo; echo "--- port status"; ubnt-systool portstatus 2>/dev/null || true
'
fi
if want logs; then
remote_script+='
echo; echo "### logs (last 60 relevant lines)"
for f in /var/log/messages /var/log/syslog; do
  [ -r "$f" ] && { grep -Ei "wan|dhcp|link (up|down)|carrier|disconnect|roam|kernel: eth|oom|reboot" "$f" | tail -60; break; }
done
command -v journalctl >/dev/null 2>&1 && journalctl --no-pager -n 40 -p warning 2>/dev/null | tail -40
'
fi

exec ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new "$target" "sh -c '$(printf '%s' "$remote_script" | sed "s/'/'\\\\''/g")'"
