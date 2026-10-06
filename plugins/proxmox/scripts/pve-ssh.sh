#!/usr/bin/env bash
# pve-ssh.sh - run a command on a Proxmox VE node over SSH (second access tier).
#
# Usage: pve-ssh.sh [-n HOST] [--check] <command> [args...]
#
# Each argument is shell-quoted (printf %q) and the result is passed to ssh as
# one remote command string, so whitespace and special characters survive.
# --check runs "pveversion" and cannot be combined with a command.
#
# Environment:
#   PVE_SSH_HOST  node to connect to (default: host part of PVE_HOST)
#   PVE_SSH_USER  SSH user (default root)
#   PVE_SSH_PORT  SSH port (default 22)
#   PVE_SSH_KEY   private key file passed to ssh -i
#   PVE_SSH_OPTS  extra ssh options, word-split (for example "-o StrictHostKeyChecking=accept-new")
#
# Exit codes: exit code of the remote command; 1 usage, no host or no ssh;
#             255 ssh connection failure (ssh's own code).
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: pve-ssh.sh [-n HOST] [--check] <command> [args...]

  -n HOST   node to connect to (overrides PVE_SSH_HOST and PVE_HOST)
  --check   run "pveversion" on the node to verify access (no command allowed)

Each argument is shell-quoted for the remote side (printf %q) and the whole
command is sent as one string, so "-comment 'two words'" stays one argument.

Env: PVE_SSH_HOST (default: host part of PVE_HOST) PVE_SSH_USER (root)
     PVE_SSH_PORT (22) PVE_SSH_KEY PVE_SSH_OPTS
Runs: ssh -o BatchMode=yes -o ConnectTimeout=10 [-p PORT] [-i KEY] $PVE_SSH_OPTS USER@HOST -- 'command args...'
Exit: remote exit code | 1 usage/no host | 255 ssh failure
EOF
}

host=""
check=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -n)
      [ "$#" -ge 2 ] || { printf 'pve-ssh: -n needs a host\n' >&2; exit 1; }
      host="$2"; shift 2 ;;
    --check) check=1; shift ;;
    --) shift; break ;;
    -*)
      printf 'pve-ssh: unknown option %s\n' "$1" >&2; exit 1 ;;
    *) break ;;
  esac
done

if [ "$check" -eq 1 ]; then
  if [ "$#" -gt 0 ]; then
    printf 'pve-ssh: --check cannot be combined with a command (got: %s)\n' "$1" >&2
    exit 1
  fi
  set -- pveversion
fi
if [ "$#" -eq 0 ]; then
  printf 'pve-ssh: no command given (see --help)\n' >&2
  exit 1
fi

if [ -z "$host" ]; then
  host="${PVE_SSH_HOST:-}"
fi
if [ -z "$host" ] && [ -n "${PVE_HOST:-}" ]; then
  # Derive the host part: drop scheme, path, port and IPv6 brackets.
  h="${PVE_HOST#*://}"
  h="${h%%/*}"
  if [[ "$h" == \[* ]]; then
    h="${h#[}"
    h="${h%%]*}"
  else
    h="${h%%:*}"
  fi
  host="$h"
fi
if [ -z "$host" ]; then
  printf 'pve-ssh: no host: pass -n HOST or set PVE_SSH_HOST or PVE_HOST\n' >&2
  exit 1
fi

command -v ssh >/dev/null 2>&1 || { printf 'pve-ssh: ssh is required but not installed\n' >&2; exit 1; }

user="${PVE_SSH_USER:-root}"
port="${PVE_SSH_PORT:-22}"
ssh_args=(-o BatchMode=yes -o ConnectTimeout=10)
if [ "$port" != "22" ]; then
  ssh_args+=(-p "$port")
fi
if [ -n "${PVE_SSH_KEY:-}" ]; then
  ssh_args+=(-i "$PVE_SSH_KEY")
fi
if [ -n "${PVE_SSH_OPTS:-}" ]; then
  # Word-split the extra options on purpose.
  read -r -a extra <<<"$PVE_SSH_OPTS"
  ssh_args+=("${extra[@]}")
fi

# Quote every argument so the remote shell sees the same words we were given.
remote_cmd="$(printf '%q ' "$@")"
remote_cmd="${remote_cmd% }"
exec ssh "${ssh_args[@]}" "$user@$host" -- "$remote_cmd"

