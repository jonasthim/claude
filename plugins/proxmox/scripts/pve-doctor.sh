#!/usr/bin/env bash
# pve-doctor.sh - check tooling, environment, connectivity and token capability
# for the Proxmox VE scripts in this directory.
#
# Usage: pve-doctor.sh (no arguments)
#
# Output lines are prefixed [ok], [warn], [fail] or [info].
# Exit codes: 0 all checks passed; 1 missing prerequisite or env var;
#             2 transport/TLS failure; 3 HTTP 401 (bad token);
#             4 HTTP 403 (token lacks permission); 5 other API error.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: pve-doctor.sh

Checks curl/jq, PVE_* environment, GET /version, GET /access/permissions,
GET /nodes, GET /cluster/status and (if PVE_SSH_HOST or PVE_SSH_USER is set)
SSH access via pve-ssh.sh. Never prints PVE_TOKEN_SECRET.
Exit: 0 ok | 1 prereq/env | 2 transport/TLS | 3 HTTP 401 | 4 HTTP 403 | 5 other API error
EOF
}

for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
  esac
done

here="$(dirname "${BASH_SOURCE[0]}")"
api="$here/pve-api.sh"
sshtool="$here/pve-ssh.sh"
rc=0

ok()   { printf '[ok] %s\n' "$1"; }
warn() { printf '[warn] %s\n' "$1"; }
info() { printf '[info] %s\n' "$1"; }
fail() { printf '[fail] %s\n' "$1"; }

# Keep the first non-zero exit code seen; later failures do not override it.
set_rc() {
  if [ "$rc" -eq 0 ]; then
    rc="$1"
  fi
}

# Run an API call, print its stderr as [fail]/[warn] lines and map the result
# to a doctor exit code: 2 transport, 3 HTTP 401, 4 HTTP 403, 5 other.
# Sets api_out and api_code. Returns the pve-api.sh exit code.
api_out=""
api_code=0
call_api() {
  local errfile
  errfile="$(mktemp)"
  set +e
  api_out="$("$api" GET "$@" 2>"$errfile")"
  local r=$?
  set -e
  api_err="$(cat "$errfile")"
  rm -f "$errfile"
  case "$r" in
    0) api_code=0 ;;
    2) api_code=2 ;;
    3)
      if printf '%s' "$api_err" | grep -q 'HTTP 401'; then
        api_code=3
      elif printf '%s' "$api_err" | grep -q 'HTTP 403'; then
        api_code=4
      else
        api_code=5
      fi
      ;;
    *) api_code=5 ;;
  esac
  return "$r"
}

# 1. Prerequisites.
missing=0
for tool in curl jq; do
  if command -v "$tool" >/dev/null 2>&1; then
    ok "$tool found: $(command -v "$tool")"
  else
    fail "$tool is required but not installed"
    missing=1
  fi
done
if [ ! -x "$api" ]; then
  fail "$api is missing or not executable"
  missing=1
fi
if [ "$missing" -eq 1 ]; then
  exit 1
fi

# 2. Environment.
envfail=0
if [ -n "${PVE_HOST:-}" ]; then
  # Never echo user:pass@ userinfo that may be embedded in the URL.
  ok "PVE_HOST=$(printf '%s' "$PVE_HOST" | sed -e 's#://[^@/]*@#://#')"

else
  fail "PVE_HOST is not set (host[:port] or https://host:8006)"
  envfail=1
fi
if [ -n "${PVE_TOKEN_ID:-}" ]; then
  ok "PVE_TOKEN_ID=$PVE_TOKEN_ID"
  if [[ "$PVE_TOKEN_ID" != *@*!* ]]; then
    warn "PVE_TOKEN_ID should look like USER@REALM!TOKENID"
  fi
else
  fail "PVE_TOKEN_ID is not set (USER@REALM!TOKENID)"
  envfail=1
fi
if [ -n "${PVE_TOKEN_SECRET:-}" ]; then
  ok "PVE_TOKEN_SECRET=set (hidden)"
else
  fail "PVE_TOKEN_SECRET is not set"
  envfail=1
fi
if [ "${PVE_INSECURE:-0}" = "1" ]; then
  warn "PVE_INSECURE=1: TLS certificate verification is disabled"
elif [ -n "${PVE_CA_CERT:-}" ]; then
  if [ -r "$PVE_CA_CERT" ]; then
    ok "PVE_CA_CERT=$PVE_CA_CERT (readable)"
  else
    fail "PVE_CA_CERT=$PVE_CA_CERT is not readable"
    envfail=1
  fi
else
  info "TLS: system CA store (set PVE_CA_CERT for a self-signed cluster CA)"
fi
info "PVE_TIMEOUT=${PVE_TIMEOUT:-30}"
if [ "$envfail" -eq 1 ]; then
  exit 1
fi

# 3. GET /version.
# Response fields version, release and repoid confirmed on a PVE 9.2 cluster
# (e.g. version "9.2.20", release "9.2").
if call_api /version; then
  printf '%s\n' "$api_out" | sed -e 's/^/[info]   /'
  version="$(printf '%s' "$api_out" | jq -r '.version // empty' 2>/dev/null || true)"
  if [ -n "$version" ]; then
    ok "Proxmox VE $version"
    major="${version%%.*}"
    if [ "$major" != "9" ]; then
      warn "this plugin targets Proxmox VE 9.x; server reports $version"
    fi
  else
    warn "GET /version returned no .version field (field names UNVERIFIED)"
  fi
else
  fail "GET /version failed:"
  printf '%s\n' "$api_err" | sed -e 's/^/[fail]   /'
  case "$api_code" in
    2) fail "transport/TLS error: check PVE_HOST, port 8006, PVE_CA_CERT (or opt in with PVE_INSECURE=1)" ;;
    3) fail "HTTP 401: token id or secret rejected, or token expired" ;;
    4) fail "HTTP 403: token lacks permission for /version" ;;
    *) fail "unexpected API error" ;;
  esac
  exit "$api_code"
fi

# 4. GET /access/permissions - capability summary.
# Shape confirmed on a PVE 9.2 cluster: an object keyed by ACL path ("/",
# "/storage", "/sdn", "/vms", "/access", ...), each value an object
# {privilege-name: 1}, not an array. The recursive scan below collects the
# privilege keys from every path. A PVEAuditor token on / yields exactly
# seven: Datastore.Audit Mapping.Audit Pool.Audit SDN.Audit Sys.Audit
# VM.Audit VM.GuestAgent.Audit (read-only, 7 distinct privileges seen).
if call_api /access/permissions; then
  privs="$(printf '%s' "$api_out" \
    | jq -r '[.. | objects | keys[] | select(test("^[A-Z][A-Za-z]+\\.[A-Za-z.]+$"))] | unique | .[]' 2>/dev/null || true)"
  if [ -z "$privs" ]; then
    warn "GET /access/permissions returned no recognisable privileges (shape UNVERIFIED)"
  else
    count="$(printf '%s\n' "$privs" | wc -l | tr -d ' ')"
    level="read-only"
    if printf '%s\n' "$privs" | grep -Eq '^(VM\.(PowerMgmt|Allocate|Clone|Migrate|Snapshot|Snapshot\.Rollback|Config\.[A-Za-z]+|Backup)|Datastore\.AllocateSpace|SDN\.Use)$'; then
      level="operator"
    fi
    if printf '%s\n' "$privs" | grep -Eq '^(Sys\.Modify|Sys\.PowerMgmt|Datastore\.Allocate|SDN\.Allocate)$'; then
      level="admin-capable"
    fi
    ok "token capability: $level ($count distinct privileges seen)"
    info "privileges: $(printf '%s\n' "$privs" | tr '\n' ' ' | sed -e 's/ $//')"
    if ! printf '%s\n' "$privs" | grep -Eq '^(Sys\.Audit|VM\.Audit|Datastore\.Audit)$'; then
      warn "no *.Audit privilege seen; inventory calls may return 403"
    fi
  fi
else
  warn "GET /access/permissions failed (capability summary skipped):"
  printf '%s\n' "$api_err" | sed -e 's/^/[warn]   /'
fi

# 5. GET /nodes.
# UNVERIFIED: item fields "node" and "status" of GET /nodes are assumed.
if call_api /nodes; then
  nodes="$(printf '%s' "$api_out" \
    | jq -r 'if type=="array" then .[] | "\(.node // .id // "?") (\(.status // "unknown"))" else empty end' 2>/dev/null || true)"
  if [ -n "$nodes" ]; then
    ok "nodes: $(printf '%s\n' "$nodes" | tr '\n' ',' | sed -e 's/,$//' -e 's/,/, /g')"
  else
    warn "GET /nodes returned no nodes"
  fi
else
  fail "GET /nodes failed:"
  printf '%s\n' "$api_err" | sed -e 's/^/[fail]   /'
  set_rc "$api_code"
fi

# 6. GET /cluster/status (best effort).
# Shape confirmed on a PVE 9.2 cluster: a mixed array; exactly one
# type=cluster item (id, name, nodes, quorate, type, version) and one
# type=node item per node (id, ip, level, local, name, nodeid, online, type).
# The jq below branches on type, as any consumer of this list must.
if call_api /cluster/status; then
  cname="$(printf '%s' "$api_out" | jq -r '[.[]? | select(.type=="cluster")][0] | .name // empty' 2>/dev/null || true)"
  quorate="$(printf '%s' "$api_out" | jq -r '[.[]? | select(.type=="cluster")][0] | .quorate // empty' 2>/dev/null || true)"
  ncount="$(printf '%s' "$api_out" | jq -r '[.[]? | select(.type=="node")] | length' 2>/dev/null || echo 0)"
  if [ -n "$cname" ]; then
    if [ "$quorate" = "1" ] || [ "$quorate" = "true" ]; then
      ok "cluster $cname: quorate, $ncount node(s)"
    else
      warn "cluster $cname: NOT quorate (quorate=$quorate), $ncount node(s)"
    fi
  else
    info "no cluster entry in /cluster/status (standalone node or shape UNVERIFIED); $ncount node item(s)"
  fi
else
  warn "GET /cluster/status failed (best effort):"
  printf '%s\n' "$api_err" | sed -e 's/^/[warn]   /'
fi

# 7. Optional SSH tier.
if [ -n "${PVE_SSH_HOST:-}" ] || [ -n "${PVE_SSH_USER:-}" ]; then
  if [ -x "$sshtool" ]; then
    set +e
    sshout="$("$sshtool" pveversion 2>&1)"
    sshrc=$?
    set -e
    if [ "$sshrc" -eq 0 ]; then
      ok "ssh tier: $(printf '%s' "$sshout" | head -n 1)"
    else
      warn "ssh tier check failed (exit $sshrc): $(printf '%s' "$sshout" | head -n 1)"
    fi
  else
    warn "$sshtool is missing or not executable; ssh tier not checked"
  fi
else
  info "ssh tier not configured (set PVE_SSH_HOST to enable node-level commands)"
fi

if [ "$rc" -eq 0 ]; then
  ok "doctor finished: all checks passed"
else
  fail "doctor finished with errors (exit $rc)"
fi
exit "$rc"
