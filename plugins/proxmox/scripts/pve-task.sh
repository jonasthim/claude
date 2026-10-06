#!/usr/bin/env bash
# pve-task.sh - wait for a Proxmox VE task (UPID) to finish and print its log.
#
# Usage: pve-task.sh <UPID|-> [--timeout SECS] [--interval SECS] [--no-log]
#
# Polls GET /nodes/<node>/tasks/<upid>/status until .status == "stopped",
# then prints the task log lines and a final "exitstatus: <value>" line.
# Only "OK" and "WARNINGS: n" count as success.
#
# Exit codes: 0 task OK or WARNINGS; 1 task failed; 2 API/transport error;
#             3 usage or bad UPID; 4 timeout waiting for the task.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: pve-task.sh <UPID|-> [--timeout SECS] [--interval SECS] [--no-log]

  UPID        task id as returned by the API (UPID:node:...); "-" reads stdin
  --timeout   give up after SECS seconds (default 600)
  --interval  poll interval in seconds (default 2)
  --no-log    do not print the task log, only the exit status

Uses pve-api.sh next to this script (same PVE_* environment).
Exit: 0 OK/WARNINGS | 1 task failed | 2 API error | 3 usage/bad UPID | 4 timeout
EOF
}

api="$(dirname "${BASH_SOURCE[0]}")/pve-api.sh"
upid=""
timeout=600
interval=2
show_log=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --timeout)
      [ "$#" -ge 2 ] || { printf 'pve-task: --timeout needs a value\n' >&2; exit 3; }
      timeout="$2"; shift 2 ;;
    --interval)
      [ "$#" -ge 2 ] || { printf 'pve-task: --interval needs a value\n' >&2; exit 3; }
      interval="$2"; shift 2 ;;
    --no-log) show_log=0; shift ;;
    -)
      upid="$(head -n 1 | tr -d '\r')"; shift ;;
    -*)
      printf 'pve-task: unknown option %s\n' "$1" >&2; exit 3 ;;
    *)
      if [ -n "$upid" ]; then
        printf 'pve-task: only one UPID allowed\n' >&2; exit 3
      fi
      upid="$1"; shift ;;
  esac
done

upid="${upid//[[:space:]]/}"
if [ -z "$upid" ]; then
  printf 'pve-task: missing UPID (see --help)\n' >&2
  exit 3
fi
if [[ "$upid" != UPID:* ]]; then
  printf 'pve-task: not a UPID: %s\n' "$upid" >&2
  exit 3
fi
if ! [[ "$timeout" =~ ^[0-9]+([.][0-9]+)?$ ]] || ! [[ "$interval" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  printf 'pve-task: --timeout and --interval must be numbers\n' >&2
  exit 3
fi

command -v jq >/dev/null 2>&1 || { printf 'pve-task: jq is required\n' >&2; exit 3; }

# The node is the second colon-separated field of the UPID.
node="$(printf '%s' "$upid" | cut -d: -f2)"
if [ -z "$node" ]; then
  printf 'pve-task: UPID has no node field: %s\n' "$upid" >&2
  exit 3
fi
enc="$(jq -rn --arg u "$upid" '$u|@uri')"
status_path="/nodes/$node/tasks/$enc/status"
log_path="/nodes/$node/tasks/$enc/log"

start_ts="$(date +%s)"
timeout_int="${timeout%%.*}"
status_json=""
while :; do
  if ! status_json="$("$api" GET "$status_path")"; then
    printf 'pve-task: could not read task status for %s\n' "$upid" >&2
    exit 2
  fi
  state="$(printf '%s' "$status_json" | jq -r '.status // empty' 2>/dev/null || true)"
  if [ "$state" = "stopped" ]; then
    break
  fi
  now="$(date +%s)"
  if [ $((now - start_ts)) -ge "$timeout_int" ]; then
    printf 'pve-task: timeout after %ss waiting for %s (status: %s)\n' \
      "$timeout" "$upid" "${state:-unknown}" >&2
    exit 4
  fi
  sleep "$interval"
done

if [ "$show_log" -eq 1 ]; then
  start=0
  limit=500
  while :; do
    if ! page="$("$api" GET "$log_path" "start=$start" "limit=$limit")"; then
      printf 'pve-task: could not read task log for %s\n' "$upid" >&2
      exit 2
    fi
    count="$(printf '%s' "$page" | jq -r 'if type=="array" then length else 0 end' 2>/dev/null || echo 0)"
    if [ "$count" -gt 0 ]; then
      printf '%s' "$page" | jq -r '.[] | .t // empty'
    fi
    if [ "$count" -lt "$limit" ]; then
      break
    fi
    start=$((start + count))
  done
fi

exitstatus="$(printf '%s' "$status_json" | jq -r '.exitstatus // "unknown"')"
printf 'exitstatus: %s\n' "$exitstatus"
case "$exitstatus" in
  OK|WARNINGS*) exit 0 ;;
  *)
    printf 'pve-task: task failed: %s\n' "$exitstatus" >&2
    exit 1
    ;;
esac
