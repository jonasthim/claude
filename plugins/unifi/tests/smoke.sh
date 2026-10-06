#!/usr/bin/env bash
# Offline smoke test: runs unifi.py against the evals fixtures, no console needed.
# Usage: bash tests/smoke.sh
set -u
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cli="$root/skills/unifi-ops/scripts/unifi.py"
export UNIFI_MOCK_DIR="$root/skills/unifi-ops/evals/fixtures" UNIFI_CACHE_DIR=0
unset UNIFI_HOST UNIFI_API_KEY UNIFI_CLOUD_API_KEY UNIFI_SITE_MANAGER_API_KEY
passed=0 failed=0

expect() { # expect <exit code> <args...>
  local want=$1; shift
  python3 -I "$cli" "$@" >/dev/null 2>&1
  local got=$?
  if [ "$got" -eq "$want" ]; then passed=$((passed + 1)); echo "PASS unifi.py $* -> $got"
  else failed=$((failed + 1)); echo "FAIL unifi.py $*: exit $got, want $want"; fi
}

for cmd in "info" "sites list" "devices list" "clients list" "networks list" "wifi list" \
           "report health" "cloud hosts"; do
  # shellcheck disable=SC2086
  expect 0 $cmd
done
# The write gate: a mutating call without --yes is refused with exit 3; --dry-run never sends.
expect 3 devices restart d1a1-gw
expect 0 devices restart d1a1-gw --dry-run
# Incomplete action and adopt calls are refused locally (exit 1) before any request.
expect 1 devices action d1a1-gw --yes
expect 1 clients action c1 --yes
expect 1 devices adopt --yes
expect 0 devices adopt --macs aa:bb:cc:dd:ee:ff --dry-run

# ssh_diag.sh rejects an unknown section before it opens SSH.
if bash "$root/skills/unifi-ops/scripts/ssh_diag.sh" host sytem >/dev/null 2>&1; then
  failed=$((failed + 1)); echo "FAIL ssh_diag.sh accepted an unknown section"
else passed=$((passed + 1)); echo "PASS ssh_diag.sh rejects an unknown section"; fi

echo
echo "summary: $passed passed, $failed failed"
[ "$failed" -eq 0 ]
