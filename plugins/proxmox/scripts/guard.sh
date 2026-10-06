#!/usr/bin/env bash
# guard.sh - Claude Code PreToolUse hook for the Bash tool.
#
# Reads the hook JSON from stdin, extracts .tool_input.command and asks for
# confirmation when the command is a destructive or disruptive Proxmox VE
# action. It never blocks on its own: the only output is an "ask" decision
# for the first matching rule, so a human approves or rejects the prompt.
#
# Always exits 0. No match: no output. Match: one JSON object on stdout:
# {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask",
#   "permissionDecisionReason":"Proxmox guard: <reason>. Matched: <text>. Confirm with the user before running."}}
#
# There is no bypass variable by design: disable the plugin to turn it off.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: guard.sh < hook-input.json

PreToolUse hook for the Bash tool. Reads {"tool_input":{"command":"..."}} on
stdin and prints a permissionDecision "ask" JSON object when the command is a
destructive or disruptive Proxmox VE action (24 rules, see the script).
Always exits 0.
EOF
}

for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
  esac
done

# Read stdin exactly once; tolerate an empty or closed stdin.
input="$(cat 2>/dev/null || true)"
if [ -z "$input" ]; then
  exit 0
fi

have_jq=0
if command -v jq >/dev/null 2>&1; then
  have_jq=1
fi

if [ "$have_jq" -eq 1 ]; then
  # Malformed JSON yields an empty command and therefore no prompt.
  cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null || true)"
else
  # Without jq treat the raw stdin as the command text (best effort).
  cmd="$input"
fi
if [ -z "$cmd" ]; then
  exit 0
fi

# Normalise: newlines and tabs become spaces, runs of spaces collapse.
cmd="$(printf '%s' "$cmd" | tr '\n\t' '  ' | tr -s ' ')"

# Emit the ask decision for the given reason and matched fragment, then exit 0.
emit() {
  local reason="$1" matched="$2" text
  matched="$(printf '%s' "$matched" | cut -c1-80)"
  text="Proxmox guard: $reason. Matched: $matched. Confirm with the user before running."
  if [ "$have_jq" -eq 1 ]; then
    jq -cn --arg reason "$text" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:$reason}}'
  else
    # Fallback without jq: escape backslashes and double quotes, drop control chars.
    text="$(printf '%s' "$text" | tr -d '\000-\037' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"%s"}}\n' "$text"
  fi
  exit 0
}

# Matching ignores case: pve-api.sh upper-cases its method, so "delete" is a DELETE.

# First fragment of $cmd matching an ERE (empty when there is no match).
first_match() {
  printf '%s' "$cmd" | grep -Eio -- "$1" 2>/dev/null | head -n 1 || true
}

# Test a simple rule: ask when the ERE matches the command.
rule() {
  local re="$1" reason="$2" m
  if printf '%s' "$cmd" | grep -Eiq -- "$re"; then
    m="$(first_match "$re")"
    emit "$reason" "${m:-$cmd}"
  fi
}

# Return 0 when the ERE matches the command.
has() {
  printf '%s' "$cmd" | grep -Eiq -- "$1"
}

# Rule 1: qm/pct destroy permanently deletes the guest and its disks.
rule '\b(qm|pct) +destroy\b' 'permanently deletes the guest and its disks'

# Rule 2: qm/pct stop or reset hard-stops the guest without graceful shutdown.
rule '\b(qm|pct) +(stop|reset)\b' 'hard-stops the guest without graceful shutdown'

# Rule 3: qm/pct shutdown, reboot or suspend takes the guest offline.
rule '\b(qm|pct) +(shutdown|reboot|suspend)\b' 'takes the guest offline'

# Rule 4: qm/pct rollback discards all changes since the snapshot.
rule '\b(qm|pct) +rollback\b' 'discards all changes since the snapshot'

# Rule 5: qm/pct delsnapshot deletes a snapshot.
rule '\b(qm|pct) +delsnapshot\b' 'deletes a snapshot'

# Rule 6: qm/pct migrate or remote-migrate moves the guest to another node.
rule '\b(qm|pct) +(migrate|remote-migrate)\b' 'moves the guest to another node'

# Rule 7: qm/pct template irreversibly converts the guest into a template.
rule '\b(qm|pct) +template\b' 'irreversibly converts the guest into a template'

# Rule 8: disk/volume move or unlink moves or unlinks guest storage.
rule '\b(qm|pct) +(move-disk|move_disk|move-volume|move_volume|disk +(move|unlink))\b' 'moves or unlinks guest storage'

# Rule 9: qm/pct set with --delete removes a config key or disk.
rule '\b(qm|pct) +set\b.*--?delete\b' 'removes a config key or disk'

# Rule 10: pvecm membership/quorum commands change cluster membership or quorum.
rule '\bpvecm +(delnode|expected|add|create|qdevice)\b' 'changes cluster membership or quorum'

# Rule 11: ha-manager (rules) remove/set/migrate/relocate/crm-command changes HA state or placement.
rule '\bha-manager +(rules +)?(remove|set|migrate|relocate|crm-command)\b' 'changes HA state or placement'

# Rule 12: pvenode stopall/migrateall/suspendall stops, suspends or migrates all guests on a node.
rule '\bpvenode +(stopall|migrateall|suspendall)\b' 'stops, suspends or migrates all guests on a node'

# Rule 13: pvesm free/remove/prune-backups and pveam remove delete storage volumes or templates.
rule '\bpvesm +(free|remove|prune-backups)\b|\bpveam +remove\b' 'deletes storage volumes or templates'

# Rule 14: vzdump with --remove 1 or --prune-backups prunes existing backups.
rule '\bvzdump\b.*--?(remove[ =]1|prune-backups)\b' 'prunes existing backups'

# Rule 15: reboot/poweroff/service stop over SSH reboots, powers off or stops services on a node.
rule '\b(ssh|pve-ssh\.sh)\b.*\b(reboot|shutdown|poweroff|halt|init +[06]|systemctl +(reboot|poweroff|halt|stop|restart|isolate))\b' \
  'reboots/powers off/stops services on a node'

# Rule 16: apt upgrade/remove over SSH changes packages on a node.
rule '\b(ssh|pve-ssh\.sh)\b.*\bapt(-get)? +(upgrade|dist-upgrade|full-upgrade|remove|purge|autoremove)\b' \
  'changes packages on a node'

# Rule 17: ifreload/ifdown/ifup over SSH applies node network changes.
rule '\b(ssh|pve-ssh\.sh)\b.*\b(ifreload|ifdown|ifup)\b' 'applies node network changes'

# Rule 18: pvesh delete is an HTTP DELETE via pvesh.
rule '\bpvesh +delete\b' 'HTTP DELETE via pvesh'

# Rule 19: pve-api.sh DELETE is an HTTP DELETE via pve-api.sh.
rule '\bpve-api\.sh +DELETE\b' 'HTTP DELETE via pve-api.sh'

# Rule 20: curl with -X DELETE against the API host is an HTTP DELETE against the API.
if has '\bcurl\b' && has '(-X *DELETE|--request[ =]*DELETE)' && has '(api2/|\$\{?PVE_HOST|:8006)'; then
  emit 'HTTP DELETE against the API' "$(first_match '(-X *DELETE|--request[ =]*DELETE)')"
fi

# Rule 21: disruptive POST (pvesh create, pve-api.sh POST or curl POST) to a gated path.
# Each entry: ERE for the path, then the reason. Order matters: first match wins.
post_paths=(
  '/nodes/[^/ ]+/(qemu|lxc)/[0-9]+/status/(stop|reset)\b' 'hard-stops the guest without graceful shutdown'
  '/nodes/[^/ ]+/(qemu|lxc)/[0-9]+/status/(shutdown|reboot|suspend)\b' 'takes the guest offline'
  '/nodes/[^/ ]+/(qemu|lxc)/[0-9]+/snapshot/[^/ ]+/rollback\b' 'discards all changes since the snapshot'
  '/nodes/[^/ ]+/(qemu|lxc)/[0-9]+/migrate\b' 'moves the guest to another node'
  '/nodes/[^/ ]+/(qemu|lxc)/[0-9]+/template\b' 'irreversibly converts the guest into a template'
  '/nodes/[^/ ]+/(qemu|lxc)/[0-9]+/(move_disk|move_volume)\b' 'moves or unlinks guest storage'
  '/nodes/[^/ ]+/(status|stopall|migrateall|suspendall)\b' 'reboots/shuts down the node or all its guests'
  '/cluster/bulk-action/guest/(shutdown|suspend|migrate)\b' 'takes guests offline or moves them in bulk'
  '/cluster/ha/resources/[^/ ]+/migrate\b' 'changes HA state or placement'
  '/cluster/ha/status/disarm-ha\b' 'changes HA state or placement'
  '/cluster/sdn/rollback\b' 'applies SDN configuration cluster-wide'
  '/nodes/[^/ ]+/storage/[^/ ]+/prunebackups\b' 'prunes existing backups'
  '/nodes/[^/ ]+/vzdump\b.*\b(remove[= ]1|prune-backups[= ])' 'prunes existing backups'
  # "delete" only as a parameter key (delete=..., ?delete=, &delete=, "delete=) or a
  # -delete/--delete flag, never as a word inside a value such as description=...
  '/(qemu|lxc)/[0-9]+/config\b.*(^|[^[:alnum:]_=.-])(delete=|--?delete[= ])' 'removes a config key or disk'
)
is_post=0
if has '\bpvesh +create\b' || has '\bpve-api\.sh +POST\b'; then
  is_post=1
elif has '\bcurl\b' && has '(-X *POST|--request[ =]*POST|-d |--data)'; then
  is_post=1
fi
if [ "$is_post" -eq 1 ]; then
  i=0
  while [ "$i" -lt "${#post_paths[@]}" ]; do
    if has "${post_paths[$i]}"; then
      emit "${post_paths[$((i + 1))]}" "$(first_match "${post_paths[$i]}")"
    fi
    i=$((i + 2))
  done
fi

# Rule 22: disruptive PUT (pvesh set, pve-api.sh PUT or curl PUT) to a gated path.
put_paths=(
  '/nodes/[^/ ]+/network([^/[:alnum:]_-]|$)' 'applies staged network changes (ifreload) and can cut connectivity'
  '/cluster/sdn([^/[:alnum:]_-]|$)' 'applies SDN configuration cluster-wide'
  '/cluster/ha/resources/[^/ ]+.*\bstate[= ](stopped|disabled)\b' 'changes HA state or placement'
  # Any "disable" parameter on a replication job, whatever its value: re-enabling only costs a prompt.
  '/cluster/replication/[^/ ]+.*\bdisable\b' 'pauses a storage replication job'
  # Same "delete" parameter/flag pattern as in post_paths above.
  '/(qemu|lxc)/[0-9]+/config\b.*(^|[^[:alnum:]_=.-])(delete=|--?delete[= ])' 'removes a config key or disk'

)
is_put=0

if has '\bpvesh +set\b' || has '\bpve-api\.sh +PUT\b'; then
  is_put=1
elif has '\bcurl\b' && has '(-X *PUT|--request[ =]*PUT)'; then
  is_put=1
fi
if [ "$is_put" -eq 1 ]; then
  i=0
  while [ "$i" -lt "${#put_paths[@]}" ]; do
    if has "${put_paths[$i]}"; then
      emit "${put_paths[$((i + 1))]}" "$(first_match "${put_paths[$i]}")"
    fi
    i=$((i + 2))
  done
fi

# Rule 23: rm of vzdump-* files or anything under a dump/ directory deletes backup files.
rule '\brm\b.*(vzdump-|/dump/)' 'deletes backup files'

# Rule 24: pvesr delete/disable, or update --disable, removes or pauses a storage replication job.
# The API forms are covered above: DELETE by rules 18-20, PUT with disable by rule 22.
rule '\bpvesr +(delete|disable)\b|\bpvesr +update\b.*--?disable\b' 'removes or pauses a storage replication job'

# No rule matched: stay silent so the normal permission flow applies.
exit 0
