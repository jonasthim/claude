#!/usr/bin/env bash
# run.sh - test suite for the proxmox plugin scripts, guard hook and layout.
#
# Usage: bash tests/run.sh [--allow-missing] [--no-validate]
#   --allow-missing  pass through to lint_plugin.py (interim runs)
#   --no-validate    skip "claude plugin validate --strict ."
#
# Starts tests/mock_pve.py on a random port, runs the scripts against it,
# checks the guard rule table and prints PASS/FAIL/SKIP lines plus a summary.
# Exits 1 when any check fails.
#
# Commands in tests/guard_cases.txt pass through printf '%b' before reaching
# the guard, so every backslash escape (\n, \t, \\, ...) is expanded.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

# The mock is local; make sure no HTTP(S) proxy intercepts 127.0.0.1.
export NO_PROXY=127.0.0.1,localhost
export no_proxy=127.0.0.1,localhost

allow_missing=0
no_validate=0
for arg in "$@"; do
  case "$arg" in
    --allow-missing) allow_missing=1 ;;
    --no-validate) no_validate=1 ;;
    -h|--help)
      sed -n '2,10p' "$0" | sed -e 's/^# \{0,1\}//'
      exit 0 ;;
    *) printf 'run.sh: unknown option %s\n' "$arg" >&2; exit 1 ;;
  esac
done

tmp="$root/tests/.tmp"
rm -rf "$tmp"
mkdir -p "$tmp"

passed=0
failed=0
skipped=0
pass() { printf 'PASS %s\n' "$1"; passed=$((passed + 1)); }
fail() { printf 'FAIL %s%s\n' "$1" "${2:+: $2}"; failed=$((failed + 1)); }
skip() { printf 'SKIP %s%s\n' "$1" "${2:+: $2}"; skipped=$((skipped + 1)); }

assert_eq() { # name expected actual
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected '$2', got '$3'"; fi
}
assert_contains() { # name haystack needle
  if [[ "$2" == *"$3"* ]]; then pass "$1"; else fail "$1" "output lacks '$3'"; fi
}
assert_not_contains() { # name haystack needle
  if [[ "$2" != *"$3"* ]]; then pass "$1"; else fail "$1" "output contains '$3'"; fi
}
assert_exit() { # name expected actual
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected exit $2, got $3"; fi
}

# capture <label> <cmd...>: run the command, store stdout+stderr in $out and
# in tests/.tmp/<label>.out, exit code in $rc. Never aborts the suite.
out=""
rc=0
capture() {
  local label="$1"
  shift
  set +e
  out="$("$@" 2>&1)"
  rc=$?
  set -e
  printf '%s\n' "$out" >"$tmp/$label.out"
}

api="scripts/pve-api.sh"
task="scripts/pve-task.sh"
sshtool="scripts/pve-ssh.sh"
guard="scripts/guard.sh"

# ---------------------------------------------------------------- (1) lint
lint_args=()
[ "$allow_missing" -eq 1 ] && lint_args+=(--allow-missing)
capture lint python3 -I tests/lint_plugin.py "${lint_args[@]}"
printf '%s\n' "$out" | sed -e 's/^/  lint: /' | grep -E 'FAIL|SKIP|summary' || true
assert_exit "lint_plugin.py exits 0" 0 "$rc"

# ---------------------------------------------------------------- (2) mock
python3 -I tests/mock_pve.py --port 0 >"$tmp/mock.stdout" 2>"$tmp/mock.stderr" &
mock_pid=$!
# shellcheck disable=SC2317,SC2329 # invoked by the trap below
cleanup() {
  kill "$mock_pid" 2>/dev/null || true
  wait "$mock_pid" 2>/dev/null || true
}
trap cleanup EXIT

port=""
for _ in $(seq 1 100); do
  port="$(sed -n 's/^PORT=//p' "$tmp/mock.stdout" 2>/dev/null || true)"
  [ -n "$port" ] && break
  sleep 0.1
done
if [ -z "$port" ]; then
  fail "mock server started" "no PORT line (see tests/.tmp/mock.stderr)"
  cat "$tmp/mock.stderr" >&2 || true
  exit 1
fi
pass "mock server started on port $port"

export PVE_HOST="http://127.0.0.1:$port"
export PVE_TOKEN_ID='test@pve!ci'
export PVE_TOKEN_SECRET='0123-secret'
unset PVE_INSECURE PVE_CA_CERT PVE_API_RAW PVE_API_DEBUG PVE_SSH_HOST PVE_SSH_USER || true

mock_get() { curl -sS "http://127.0.0.1:$port/__mock/$1"; }
mock_reset() { curl -sS -X POST "http://127.0.0.1:$port/__mock/reset" >/dev/null; }

# ---------------------------------------------------------------- (3) GET /version
capture api_version "$api" GET /version
assert_exit "pve-api GET /version exits 0" 0 "$rc"
assert_contains "pve-api GET /version prints version" "$out" '"version": "9.2.1"'
v1="$out"
capture api_version2 "$api" GET version
assert_eq "pve-api path without leading slash identical" "$v1" "$out"
capture api_version3 "$api" GET /api2/json/version
assert_eq "pve-api path with /api2/json prefix identical" "$v1" "$out"
capture api_version4 env "PVE_HOST=http://127.0.0.1:$port/api2/json" "$api" GET /version
assert_exit "pve-api PVE_HOST with /api2/json suffix exits 0" 0 "$rc"
assert_eq "pve-api PVE_HOST with /api2/json suffix identical" "$v1" "$out"
capture api_version5 env "PVE_HOST=http://127.0.0.1:$port/api2/json/" "$api" GET /version
assert_eq "pve-api PVE_HOST with /api2/json/ suffix identical" "$v1" "$out"
capture api_version6 env PVE_API_DEBUG=1 "PVE_HOST=http://127.0.0.1:$port/api2/json" "$api" GET /version
assert_contains "pve-api PVE_HOST suffix does not double the prefix" "$out" "pve-api: GET http://127.0.0.1:$port/api2/json/version"
assert_not_contains "pve-api PVE_HOST suffix no api2/json/api2/json" "$out" "api2/json/api2/json"

# ---------------------------------------------------------------- (4) RAW
capture api_raw env PVE_API_RAW=1 "$api" GET /version
assert_exit "pve-api RAW exits 0" 0 "$rc"
if [[ "$out" == '{"data"'* ]]; then pass "pve-api RAW output starts with {\"data\""; else fail "pve-api RAW output starts with {\"data\"" "$out"; fi

# ---------------------------------------------------------------- (5) GET params -> query
mock_reset
capture api_get_params "$api" GET /nodes/pve1/storage/local/content content=backup vmid=100
assert_exit "pve-api GET with params exits 0" 0 "$rc"
last="$(mock_get last)"
assert_eq "pve-api GET params go to the query string" "backup" "$(printf '%s' "$last" | jq -r '.query.content')"
assert_eq "pve-api GET params vmid in query" "100" "$(printf '%s' "$last" | jq -r '.query.vmid')"
assert_eq "pve-api GET sends no body" "" "$(printf '%s' "$last" | jq -r '.body')"
assert_eq "pve-api GET method recorded" "GET" "$(printf '%s' "$last" | jq -r '.method')"

# ---------------------------------------------------------------- (6) POST -> bare UPID
capture api_post_start "$api" POST /nodes/pve1/qemu/100/status/start
assert_exit "pve-api POST start exits 0" 0 "$rc"
assert_eq "pve-api POST start prints bare UPID" "UPID:pve1:000A1B2C:0001F3A4:66F00000:qmstart:100:test@pve!ci:" "$out"
last="$(mock_get last)"
assert_eq "pve-api POST without params sends no body" "" "$(printf '%s' "$last" | jq -r '.body')"

# ---------------------------------------------------------------- (7) PUT config form body
capture api_put_config "$api" PUT /nodes/pve1/qemu/100/config 'net0=virtio,bridge=vmbr0' memory=2048
assert_exit "pve-api PUT config exits 0" 0 "$rc"
assert_eq "pve-api PUT config prints null" "null" "$out"
last="$(mock_get last)"
assert_eq "pve-api PUT method recorded" "PUT" "$(printf '%s' "$last" | jq -r '.method')"
assert_contains "pve-api PUT body is url-encoded" "$(printf '%s' "$last" | jq -r '.body')" 'net0=virtio%2Cbridge%3Dvmbr0'
assert_eq "pve-api PUT form decodes net0" "virtio,bridge=vmbr0" "$(printf '%s' "$last" | jq -r '.form.net0')"
assert_eq "pve-api PUT form decodes memory" "2048" "$(printf '%s' "$last" | jq -r '.form.memory')"
ctype="$(printf '%s' "$last" | jq -r '.headers | to_entries[] | select(.key | ascii_downcase == "content-type") | .value')"
assert_contains "pve-api PUT uses form content-type" "$ctype" "application/x-www-form-urlencoded"
assert_eq "pve-api PUT sends no query" "{}" "$(printf '%s' "$last" | jq -c '.query')"

# ---------------------------------------------------------------- (8) DELETE 403
capture api_delete_403 "$api" DELETE /nodes/pve1/qemu/999
assert_exit "pve-api DELETE 403 exits 3" 3 "$rc"
assert_contains "pve-api DELETE 403 reports HTTP 403" "$out" "HTTP 403"
assert_contains "pve-api DELETE 403 reports privilege" "$out" "VM.Allocate"
last="$(mock_get last)"
assert_eq "pve-api DELETE sends no body" "" "$(printf '%s' "$last" | jq -r '.body')"

# ---------------------------------------------------------------- (9) POST 400 with errors
capture api_post_400 "$api" POST /nodes/pve1/qemu name=test
assert_exit "pve-api POST 400 exits 3" 3 "$rc"
assert_contains "pve-api POST 400 reports HTTP 400" "$out" "HTTP 400"
assert_contains "pve-api POST 400 lists param error" "$out" "vmid:"

# ---------------------------------------------------------------- (10) bogus task -> 500 -> 4
capture api_bogus_task "$api" GET "/nodes/pve1/tasks/UPID:pve1:bogus/status"
assert_exit "pve-api 500 exits 4" 4 "$rc"
assert_contains "pve-api 500 reports HTTP 500" "$out" "HTTP 500"

# ---------------------------------------------------------------- (10b) stateful mock
mock_reset
capture res_vm "$api" GET /cluster/resources type=vm
assert_exit "mock /cluster/resources type=vm exits 0" 0 "$rc"
assert_eq "mock type=vm lists only qemu/lxc" "4" "$(printf '%s' "$out" | jq '[.[] | select(.type == "qemu" or .type == "lxc")] | length')"
assert_eq "mock type=vm has no other types" "4" "$(printf '%s' "$out" | jq 'length')"
capture res_storage "$api" GET /cluster/resources type=storage
assert_eq "mock type=storage lists storages only" "4" "$(printf '%s' "$out" | jq '[.[] | select(.type == "storage")] | length')"
capture res_node "$api" GET /cluster/resources type=node
assert_eq "mock type=node lists nodes only" '["node","node"]' "$(printf '%s' "$out" | jq -c '[.[].type]')"
capture res_all "$api" GET /cluster/resources
assert_eq "mock /cluster/resources without type lists everything" "10" "$(printf '%s' "$out" | jq 'length')"
capture res_bad "$api" GET /cluster/resources type=bogus
assert_exit "mock /cluster/resources bad type exits 3" 3 "$rc"

capture ct_create "$api" POST /nodes/pve1/lxc vmid=106 'ostemplate=local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst' hostname=ct-test memory=512
assert_exit "mock create CT 106 exits 0" 0 "$rc"
assert_contains "mock create CT 106 returns a vzcreate UPID" "$out" ":vzcreate:106:"
capture ct_create_dup "$api" POST /nodes/pve1/lxc vmid=106 'ostemplate=local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst'
assert_exit "mock create CT 106 twice exits 4" 4 "$rc"
assert_contains "mock create CT 106 twice says it exists" "$out" "already exists"
capture qemu_create_novmid "$api" POST /nodes/pve1/qemu name=test
assert_exit "mock POST /nodes/pve1/qemu without vmid still 400" 3 "$rc"
capture ct_list "$api" GET /nodes/pve1/lxc
assert_eq "mock CT 106 appears in /nodes/pve1/lxc" "ct-test stopped" "$(printf '%s' "$out" | jq -r '.[] | select(.vmid == 106) | "\(.name) \(.status)"')"
capture ct_res "$api" GET /cluster/resources type=vm
assert_eq "mock CT 106 appears in /cluster/resources" "lxc pve1 stopped ct-test" "$(printf '%s' "$out" | jq -r '.[] | select(.id == "lxc/106") | "\(.type) \(.node) \(.status) \(.name)"')"
capture ct_nextid "$api" GET /cluster/nextid
assert_eq "mock nextid moves past CT 106" "107" "$out"
capture ct_config "$api" GET /nodes/pve1/lxc/106/config
assert_eq "mock CT 106 config echoes hostname" "ct-test" "$(printf '%s' "$out" | jq -r '.hostname')"
assert_eq "mock CT 106 config has a digest" "40" "$(printf '%s' "$out" | jq -r '.digest | length')"
capture ct_status "$api" GET /nodes/pve1/lxc/106/status/current
assert_eq "mock CT 106 status/current is stopped" "stopped" "$(printf '%s' "$out" | jq -r '.status')"
capture ct_start "$api" POST /nodes/pve1/lxc/106/status/start
assert_exit "mock start CT 106 exits 0" 0 "$rc"
assert_contains "mock start CT 106 returns a vzstart UPID" "$out" ":vzstart:106:"
capture ct_start_task "$task" "$out" --interval 0.1 --no-log
assert_exit "mock vzstart:106 task resolves OK" 0 "$rc"
capture ct_status_running "$api" GET /nodes/pve1/lxc/106/status/current
assert_eq "mock start flips CT 106 to running" "running" "$(printf '%s' "$out" | jq -r '.status')"
capture ct_res_running "$api" GET /cluster/resources type=vm
assert_eq "mock /cluster/resources shows CT 106 running" "running" "$(printf '%s' "$out" | jq -r '.[] | select(.id == "lxc/106") | .status')"
capture ct_stop "$api" POST /nodes/pve1/lxc/106/status/shutdown
assert_contains "mock shutdown CT 106 returns a vzshutdown UPID" "$out" ":vzshutdown:106:"
capture ct_status_stopped "$api" GET /nodes/pve1/lxc/106/status/current
assert_eq "mock shutdown flips CT 106 to stopped" "stopped" "$(printf '%s' "$out" | jq -r '.status')"
capture vm_create "$api" POST /nodes/pve1/qemu vmid=300 name=vm-test memory=1024 cores=2
assert_contains "mock create VM 300 returns a qmcreate UPID" "$out" ":qmcreate:300:"
capture vm_list "$api" GET /nodes/pve1/qemu
assert_eq "mock VM 300 appears in /nodes/pve1/qemu" "vm-test 2" "$(printf '%s' "$out" | jq -r '.[] | select(.vmid == 300) | "\(.name) \(.cpus)"')"

capture ha_post "$api" POST /cluster/ha/resources sid=ct:106 state=started
assert_exit "mock HA resource POST exits 0" 0 "$rc"
capture ha_list "$api" GET /cluster/ha/resources
assert_eq "mock HA resource listed after POST" "ct:106 started" "$(printf '%s' "$out" | jq -r '.[] | "\(.sid) \(.state)"')"
capture ha_get "$api" GET /cluster/ha/resources/ct:106
assert_eq "mock HA resource GET by sid" "ct" "$(printf '%s' "$out" | jq -r '.type')"
capture ha_res "$api" GET /cluster/resources type=vm
assert_eq "mock hastate appears on the guest" "started" "$(printf '%s' "$out" | jq -r '.[] | select(.id == "lxc/106") | .hastate')"
assert_eq "mock hastate absent on other guests" "null" "$(printf '%s' "$out" | jq -r '.[] | select(.id == "qemu/100") | .hastate')"
capture ha_status "$api" GET /cluster/ha/status/current
assert_eq "mock HA status lists the service on its node" "pve1" "$(printf '%s' "$out" | jq -r '.[] | select(.type == "service" and .sid == "ct:106") | .node')"
capture ha_put "$api" PUT /cluster/ha/resources/ct:106 state=stopped
assert_exit "mock HA resource PUT exits 0" 0 "$rc"
capture ha_get2 "$api" GET /cluster/ha/resources/ct:106
assert_eq "mock HA resource PUT updates state" "stopped" "$(printf '%s' "$out" | jq -r '.state')"
capture ha_rule_post "$api" POST /cluster/ha/rules rule=keep-on-pve1 type=node-affinity resources=ct:106 nodes=pve1
assert_exit "mock HA rule POST exits 0" 0 "$rc"
capture ha_rule_get "$api" GET /cluster/ha/rules/keep-on-pve1
assert_eq "mock HA rule GET by name" "node-affinity" "$(printf '%s' "$out" | jq -r '.type')"
capture ha_rules "$api" GET /cluster/ha/rules
assert_eq "mock HA rules listed after POST" "keep-on-pve1" "$(printf '%s' "$out" | jq -r '.[].rule')"
capture ha_delete "$api" DELETE /cluster/ha/resources/ct:106
assert_exit "mock HA resource DELETE exits 0" 0 "$rc"
capture ha_list2 "$api" GET /cluster/ha/resources
assert_eq "mock HA resource gone after DELETE" "[]" "$out"

capture snap_create "$api" POST /nodes/pve1/qemu/200/snapshot snapname=pre-upgrade description=before
assert_exit "mock snapshot create exits 0" 0 "$rc"
assert_contains "mock snapshot create returns a qmsnapshot UPID" "$out" ":qmsnapshot:200:"
capture snap_list "$api" GET /nodes/pve1/qemu/200/snapshot
assert_eq "mock snapshot list shows the new snapshot" "clean-install pre-upgrade current" "$(printf '%s' "$out" | jq -r '[.[].name] | join(" ")')"
assert_eq "mock snapshot list parent chain" "clean-install" "$(printf '%s' "$out" | jq -r '.[] | select(.name == "pre-upgrade") | .parent')"
capture snap_rollback "$api" POST /nodes/pve1/qemu/200/snapshot/pre-upgrade/rollback
assert_contains "mock snapshot rollback returns a qmrollback UPID" "$out" ":qmrollback:200:"
capture snap_101 "$api" GET /nodes/pve1/qemu/101/snapshot
assert_eq "mock VM 101 snapshot list is current only" '["current"]' "$(printf '%s' "$out" | jq -c '[.[].name]')"
capture snap_delete "$api" DELETE /nodes/pve1/qemu/200/snapshot/pre-upgrade
assert_contains "mock snapshot delete returns a qmdelsnapshot UPID" "$out" ":qmdelsnapshot:200:"
capture snap_list2 "$api" GET /nodes/pve1/qemu/200/snapshot
assert_eq "mock snapshot gone after DELETE" "clean-install current" "$(printf '%s' "$out" | jq -r '[.[].name] | join(" ")')"
capture net_list "$api" GET /nodes/pve1/network
assert_eq "mock /nodes/pve1/network lists the bridge" "bridge" "$(printf '%s' "$out" | jq -r '.[] | select(.iface == "vmbr0") | .type')"
capture pve2_storage "$api" GET /nodes/pve2/storage
assert_eq "mock /nodes/pve2/storage lists local-lvm" "local-lvm" "$(printf '%s' "$out" | jq -r '.[1].storage')"

capture ct_snap "$api" POST /nodes/pve1/lxc/106/snapshot snapname=first
assert_contains "mock CT snapshot create returns a vzsnapshot UPID" "$out" ":vzsnapshot:106:"
mock_reset
capture reset_lxc "$api" GET /nodes/pve1/lxc
assert_eq "mock reset clears created guests" "[]" "$out"
capture reset_nextid "$api" GET /cluster/nextid
assert_eq "mock reset restores nextid" "106" "$out"
capture reset_rules "$api" GET /cluster/ha/rules
assert_eq "mock reset clears HA rules" "[]" "$out"
capture reset_snaps "$api" GET /nodes/pve1/qemu/200/snapshot
assert_eq "mock reset restores fixture snapshots" "2" "$(printf '%s' "$out" | jq 'length')"
capture reset_status "$api" GET /nodes/pve1/qemu/101/status/current
assert_eq "mock reset restores guest status" "stopped" "$(printf '%s' "$out" | jq -r '.status')"

# ---------------------------------------------------------------- (11) auth/env/transport
capture api_wrong_secret env PVE_TOKEN_SECRET=wrong-secret "$api" GET /version
assert_exit "pve-api wrong secret exits 3" 3 "$rc"
assert_contains "pve-api wrong secret reports HTTP 401" "$out" "HTTP 401"
capture api_missing_secret env -u PVE_TOKEN_SECRET "$api" GET /version
assert_exit "pve-api missing secret exits 1" 1 "$rc"
assert_contains "pve-api missing secret names the variable" "$out" "PVE_TOKEN_SECRET"
capture api_missing_host env -u PVE_HOST "$api" GET /version
assert_exit "pve-api missing host exits 1" 1 "$rc"
capture api_bad_host env PVE_HOST=http://127.0.0.1:1 PVE_TIMEOUT=5 "$api" GET /version
assert_exit "pve-api unreachable host exits 2" 2 "$rc"
assert_contains "pve-api unreachable host reports curl failure" "$out" "curl failed"
capture api_usage "$api"
assert_exit "pve-api without args exits 1" 1 "$rc"
capture api_help "$api" --help
assert_exit "pve-api --help exits 0" 0 "$rc"
assert_contains "pve-api --help prints usage" "$out" "Usage:"
capture api_bad_method "$api" PATCH /version
assert_exit "pve-api unsupported method exits 1" 1 "$rc"
capture api_insecure env PVE_INSECURE=1 "$api" GET /version
assert_exit "pve-api PVE_INSECURE=1 exits 0" 0 "$rc"
assert_contains "pve-api PVE_INSECURE=1 warns" "$out" "PVE_INSECURE=1"

# ---------------------------------------------------------------- (12) debug never leaks the secret
capture api_debug env PVE_API_DEBUG=1 "$api" GET /nodes
assert_exit "pve-api DEBUG exits 0" 0 "$rc"
assert_contains "pve-api DEBUG prints method and URL" "$out" "pve-api: GET http://127.0.0.1:$port/api2/json/nodes"
assert_not_contains "pve-api DEBUG does not print the secret" "$out" "0123-secret"
assert_not_contains "pve-api DEBUG does not print the auth header" "$out" "PVEAPIToken"
mock_get last >"$tmp/mock_last_after_debug.out"
assert_contains "mock masks the auth header" "$(cat "$tmp/mock_last_after_debug.out")" "PVEAPIToken=<masked>"

# ---------------------------------------------------------------- (13) pve-task qmstart
mock_reset
capture task_qmstart "$task" "UPID:pve1:000A1B2C:0001F3A4:66F00000:qmstart:100:test@pve!ci:" --interval 0.1
assert_exit "pve-task qmstart exits 0" 0 "$rc"
assert_contains "pve-task qmstart prints log" "$out" "TASK OK"
assert_contains "pve-task qmstart prints exitstatus" "$out" "exitstatus: OK"
last_line="$(printf '%s\n' "$out" | tail -n 1)"
assert_eq "pve-task last stdout line is exitstatus" "exitstatus: OK" "$last_line"
status_calls="$(mock_get calls | jq '[.[] | select(.path | test("/tasks/.*/status$"))] | length')"
if [ "$status_calls" -ge 3 ]; then pass "pve-task polled status >= 3 times ($status_calls)"; else fail "pve-task polled status >= 3 times" "$status_calls"; fi
log_calls="$(mock_get calls | jq '[.[] | select(.path | test("/tasks/.*/log$"))] | length')"
assert_eq "pve-task fetched the log once" "1" "$log_calls"
assert_contains "pve-task URL-encodes the UPID" "$(mock_get calls | jq -r '.[-1].path')" "UPID%3Apve1%3A"
capture task_nolog "$task" "UPID:pve1:000A1B2C:0001F3A4:66F00000:qmstart:100:test@pve!ci:" --interval 0.1 --no-log
assert_exit "pve-task --no-log exits 0" 0 "$rc"
assert_not_contains "pve-task --no-log omits log" "$out" "starting task"
mock_reset
set +e
task_err="$(env PVE_INSECURE=1 "$task" "UPID:pve1:000A1B2C:0001F3A4:66F00000:qmstart:100:test@pve!ci:" --interval 0.1 2>&1 >/dev/null)"
task_rc=$?
set -e
printf '%s\n' "$task_err" >"$tmp/task_insecure.err"
assert_exit "pve-task PVE_INSECURE=1 exits 0" 0 "$task_rc"
warn_count="$(printf '%s\n' "$task_err" | grep -c 'PVE_INSECURE=1' || true)"
assert_eq "pve-task PVE_INSECURE=1 warns exactly once on stderr" "1" "$warn_count"
polls="$(mock_get calls | jq '[.[] | select(.path | test("/tasks/"))] | length')"
if [ "$polls" -ge 3 ]; then pass "pve-task PVE_INSECURE=1 made >= 3 API calls ($polls)"; else fail "pve-task PVE_INSECURE=1 made >= 3 API calls" "$polls"; fi

# ---------------------------------------------------------------- (14) qmstop failed
capture task_qmstop "$task" "UPID:pve1:000A1B2D:0001F3A5:66F00001:qmstop:101:test@pve!ci:" --interval 0.1
assert_exit "pve-task failed task exits 1" 1 "$rc"
assert_contains "pve-task failed task says task failed" "$out" "task failed"
assert_contains "pve-task failed task prints exitstatus" "$out" "exitstatus: command 'kill' failed: exit code 1"

# ---------------------------------------------------------------- (15) vzdump warnings
capture task_vzdump "$task" "UPID:pve1:000D1E2F:0004F3A4:66F00030:vzdump:102:test@pve!ci:" --interval 0.1
assert_exit "pve-task WARNINGS exits 0" 0 "$rc"
assert_contains "pve-task WARNINGS prints exitstatus" "$out" "exitstatus: WARNINGS: 1"

# ---------------------------------------------------------------- (16) stdin, bad UPIDs, timeout
set +e
out="$(printf 'UPID:pve1:000B1C2D:0002F3A4:66F00010:vzcreate:106:test@pve!ci:\n' | "$task" - --interval 0.1 2>&1)"
rc=$?
set -e
printf '%s\n' "$out" >"$tmp/task_stdin.out"
assert_exit "pve-task reads UPID from stdin" 0 "$rc"
assert_contains "pve-task stdin prints exitstatus" "$out" "exitstatus: OK"
capture task_null "$task" null
assert_exit "pve-task 'null' exits 3" 3 "$rc"
capture task_noarg "$task"
assert_exit "pve-task without UPID exits 3" 3 "$rc"
capture task_help "$task" --help
assert_exit "pve-task --help exits 0" 0 "$rc"
assert_contains "pve-task --help documents exit 3 as jq missing" "$out" "jq missing"
capture task_bogus "$task" "UPID:pve1:bogus:x" --interval 0.1
assert_exit "pve-task bogus UPID (API 500) exits 2" 2 "$rc"
capture task_forever "$task" "UPID:pve1:000E1F2A:0005F3A4:66F00040:qmforever:100:test@pve!ci:" --timeout 1 --interval 0.2
assert_exit "pve-task running task times out with 4" 4 "$rc"
assert_contains "pve-task timeout names the UPID" "$out" "qmforever:100"

# ---------------------------------------------------------------- (17) dry run and doctor calls
mock_reset
capture api_dry_post env PVE_DRY_RUN=1 "$api" POST /nodes/pve1/qemu/100/config 'net0=virtio,bridge=vmbr0' memory=2048
assert_exit "pve-api dry run POST exits 0" 0 "$rc"
if printf '%s' "$out" | jq -e . >/dev/null 2>&1; then pass "pve-api dry run prints JSON"; else fail "pve-api dry run prints JSON" "$out"; fi
assert_eq "pve-api dry run reports the method" "POST" "$(printf '%s' "$out" | jq -r '.method' 2>/dev/null || true)"
assert_eq "pve-api dry run reports the full URL" "http://127.0.0.1:$port/api2/json/nodes/pve1/qemu/100/config" "$(printf '%s' "$out" | jq -r '.url' 2>/dev/null || true)"
assert_eq "pve-api dry run keeps = inside a value" "virtio,bridge=vmbr0" "$(printf '%s' "$out" | jq -r '.params.net0' 2>/dev/null || true)"
assert_eq "pve-api dry run params are strings" "2048" "$(printf '%s' "$out" | jq -r '.params.memory' 2>/dev/null || true)"
assert_eq "pve-api dry run makes no request" "0" "$(mock_get calls | jq 'length')"
capture api_dry_get env PVE_DRY_RUN=1 "$api" GET /version
assert_exit "pve-api dry run GET exits 0" 0 "$rc"
assert_eq "pve-api dry run without params prints {}" "{}" "$(printf '%s' "$out" | jq -c '.params' 2>/dev/null || true)"
assert_eq "pve-api dry run GET makes no request" "0" "$(mock_get calls | jq 'length')"
capture api_dry_no_secret env -u PVE_TOKEN_SECRET PVE_DRY_RUN=1 "$api" GET /version
assert_exit "pve-api dry run without PVE_TOKEN_SECRET exits 1" 1 "$rc"
capture api_dry_bad_param env PVE_DRY_RUN=1 "$api" GET /version bogus
assert_exit "pve-api dry run with a bad parameter exits 1" 1 "$rc"
assert_not_contains "pve-api dry run never prints the secret" "$out" "0123-secret"
capture api_dry_userinfo env PVE_HOST="http://user:hunter2@127.0.0.1:$port" PVE_DRY_RUN=1 "$api" GET /version
assert_exit "pve-api dry run with userinfo exits 0" 0 "$rc"
assert_not_contains "pve-api dry run strips userinfo from the URL" "$out" "hunter2"
capture api_dry_dashkey env PVE_DRY_RUN=1 "$api" GET /version -x=1
assert_exit "pve-api dry run accepts a key starting with -" 0 "$rc"
assert_eq "pve-api dry run keeps a key starting with -" "1" "$(printf '%s' "$out" | jq -r '.params["-x"]')"
capture api_help_dry "$api" --help
assert_contains "pve-api --help documents PVE_DRY_RUN" "$out" "PVE_DRY_RUN"
# The four read-only calls the doctor skill runs.
capture doctor_version "$api" GET /version
assert_exit "doctor call GET /version exits 0" 0 "$rc"
assert_contains "doctor call GET /version prints version" "$out" "9.2.1"
capture doctor_perms "$api" GET /access/permissions
assert_exit "doctor call GET /access/permissions exits 0" 0 "$rc"
assert_contains "doctor call permissions show an operator privilege" "$out" "VM.PowerMgmt"
assert_not_contains "doctor call permissions have no Sys.Modify" "$out" "Sys.Modify"
capture doctor_nodes "$api" GET /nodes
assert_exit "doctor call GET /nodes exits 0" 0 "$rc"
assert_contains "doctor call GET /nodes lists pve1" "$out" "pve1"
capture doctor_cluster "$api" GET /cluster/status
assert_exit "doctor call GET /cluster/status exits 0" 0 "$rc"
assert_eq "doctor call cluster item is quorate" "1" "$(printf '%s' "$out" | jq -r '.[] | select(.type == "cluster") | .quorate')"

# ---------------------------------------------------------------- (18) pve-ssh
capture ssh_no_host env -u PVE_HOST -u PVE_SSH_HOST "$sshtool" pveversion
assert_exit "pve-ssh without host exits 1" 1 "$rc"
assert_contains "pve-ssh without host explains" "$out" "no host"
capture ssh_no_cmd env PVE_SSH_HOST=pve1 "$sshtool"
assert_exit "pve-ssh without command exits 1" 1 "$rc"
capture ssh_help "$sshtool" --help
assert_exit "pve-ssh --help exits 0" 0 "$rc"
assert_contains "pve-ssh --help prints usage" "$out" "Usage:"
capture ssh_check_cmd env PVE_SSH_HOST=pve1 "$sshtool" --check pveversion
assert_exit "pve-ssh --check with a command exits 1" 1 "$rc"
assert_contains "pve-ssh --check with a command explains" "$out" "cannot be combined"
# Fake ssh shim: prints each argument on its own line so we can inspect the
# remote command string pve-ssh.sh builds.
fakebin="$tmp/fakebin"
mkdir -p "$fakebin"
# shellcheck disable=SC2016 # the fake ssh expands $@ itself
printf '#!/usr/bin/env bash\nfor a in "$@"; do printf "%%s\\n" "$a"; done\n' >"$fakebin/ssh"
chmod +x "$fakebin/ssh"
capture ssh_quoting env PATH="$fakebin:$PATH" "$sshtool" -n pve1 pveum user add t@pve -comment "two words"
assert_exit "pve-ssh via shim exits 0" 0 "$rc"
assert_contains "pve-ssh shim sees the host" "$out" "root@pve1"
assert_contains "pve-ssh quotes whitespace in remote args" "$out" '-comment two\ words'
ssh_last="$(printf '%s\n' "$out" | tail -n 1)"
assert_eq "pve-ssh sends one remote command string" 'pveum user add t@pve -comment two\ words' "$ssh_last"
capture ssh_check_ok env PATH="$fakebin:$PATH" PVE_SSH_HOST=pve2 "$sshtool" --check
assert_exit "pve-ssh --check via shim exits 0" 0 "$rc"
assert_eq "pve-ssh --check runs pveversion" "pveversion" "$(printf '%s\n' "$out" | tail -n 1)"


# ---------------------------------------------------------------- (19) guard table
guard_input() { # command -> hook JSON on stdout
  jq -n --arg c "$1" '{session_id:"test",cwd:"/tmp",permission_mode:"default",hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$c,description:"test"},tool_use_id:"toolu_test"}'
}
cases=0
while IFS=$'\t' read -r expect raw || [ -n "$expect" ]; do
  [ -z "$expect" ] && continue
  [[ "$expect" == \#* ]] && continue
  cmd="$(printf '%b' "$raw")"
  cases=$((cases + 1))
  set +e
  gout="$(guard_input "$cmd" | "$guard" 2>"$tmp/guard_err.out")"
  grc=$?
  set -e
  label="guard $expect: $raw"
  if [ "$grc" -ne 0 ]; then
    fail "$label" "exit $grc"
    continue
  fi
  case "$expect" in
    ask)
      if printf '%s' "$gout" | jq -e '.hookSpecificOutput.permissionDecision == "ask" and .hookSpecificOutput.hookEventName == "PreToolUse" and (.hookSpecificOutput.permissionDecisionReason | length > 0)' >/dev/null 2>&1; then
        pass "$label"
      else
        fail "$label" "no valid ask decision: $gout"
      fi
      ;;
    none)
      if [ -z "$gout" ]; then pass "$label"; else fail "$label" "unexpected output: $gout"; fi
      ;;
    *) fail "$label" "unknown expectation '$expect'" ;;
  esac
done <tests/guard_cases.txt
if [ "$cases" -ge 40 ]; then pass "guard_cases.txt has >= 40 cases ($cases)"; else fail "guard_cases.txt has >= 40 cases" "$cases"; fi
set +e
gout="$(printf '' | "$guard" 2>&1)"; grc=$?
set -e
assert_exit "guard empty stdin exits 0" 0 "$grc"
assert_eq "guard empty stdin prints nothing" "" "$gout"
set +e
gout="$(printf 'not json {' | "$guard" 2>&1)"; grc=$?
set -e
assert_exit "guard malformed JSON exits 0" 0 "$grc"
assert_eq "guard malformed JSON prints nothing" "" "$gout"
set +e
gout="$(printf '{"tool_name":"Bash","tool_input":{}}' | "$guard" 2>&1)"; grc=$?
set -e
assert_exit "guard missing command exits 0" 0 "$grc"
assert_eq "guard missing command prints nothing" "" "$gout"
gout="$(guard_input 'qm stop 100' | "$guard")"
assert_contains "guard reason has the prefix" "$gout" "Proxmox guard: hard-stops the guest without graceful shutdown. Matched: qm stop. Confirm with the user before running."
capture guard_help "$guard" --help
assert_exit "guard --help exits 0" 0 "$rc"
# jq-less fallback: run with a PATH that has no jq.
nojq="$tmp/nojq-bin"
mkdir -p "$nojq"
for tool in bash cat tr grep head cut sed printf; do
  src="$(command -v "$tool" 2>/dev/null || true)"
  [ -n "$src" ] && ln -sf "$src" "$nojq/$tool"
done
set +e
gout="$(printf 'qm stop 100' | env PATH="$nojq" "$guard" 2>&1)"; grc=$?
set -e
assert_exit "guard without jq exits 0" 0 "$grc"
if printf '%s' "$gout" | jq -e '.hookSpecificOutput.permissionDecision == "ask"' >/dev/null 2>&1; then
  pass "guard without jq emits valid ask JSON"
else
  fail "guard without jq emits valid ask JSON" "$gout"
fi

# ---------------------------------------------------------------- (20) guard speed
t0="${EPOCHREALTIME/./}"
for _ in $(seq 1 20); do
  guard_input 'pve-api.sh PUT /cluster/ha/resources/vm:100 state=stopped' | "$guard" >/dev/null
done
t1="${EPOCHREALTIME/./}"
elapsed_ms=$(((t1 - t0) / 1000))
if [ "$elapsed_ms" -lt 5000 ]; then pass "20 guard runs take < 5 s (${elapsed_ms} ms)"; else fail "20 guard runs take < 5 s" "${elapsed_ms} ms"; fi

# ---------------------------------------------------------------- (12b) secret never captured anywhere
if grep -rl -- '0123-secret' "$tmp" >/dev/null 2>&1; then
  fail "secret never appears in any captured output" "$(grep -rl -- '0123-secret' "$tmp" | tr '\n' ' ')"
else
  pass "secret never appears in any captured output"
fi

# ---------------------------------------------------------------- (21) shellcheck
if command -v shellcheck >/dev/null 2>&1; then
  capture shellcheck shellcheck -x scripts/*.sh tests/run.sh
  if [ "$rc" -eq 0 ]; then pass "shellcheck clean"; else fail "shellcheck clean" "$(printf '%s' "$out" | head -n 20)"; fi
else
  skip "shellcheck" "not installed"
fi

# ---------------------------------------------------------------- (22) claude plugin validate
if [ "$no_validate" -eq 1 ]; then
  skip "claude plugin validate" "--no-validate"
elif command -v claude >/dev/null 2>&1; then
  capture validate claude plugin validate --strict .
  if [ "$rc" -eq 0 ]; then pass "claude plugin validate --strict ."; else fail "claude plugin validate --strict ." "$(printf '%s' "$out" | head -n 20)"; fi
else
  skip "claude plugin validate" "claude CLI not installed"
fi

# ---------------------------------------------------------------- summary
printf '\nsummary: %d passed, %d failed, %d skipped\n' "$passed" "$failed" "$skipped"
if [ "$failed" -gt 0 ]; then
  exit 1
fi
exit 0
