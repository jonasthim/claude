#!/usr/bin/env bash
# run.sh - test suite for the proxmox plugin scripts, guard hook and layout.
#
# Usage: bash tests/run.sh [--allow-missing] [--no-validate]
#   --allow-missing  pass through to lint_plugin.py (interim runs)
#   --no-validate    skip "claude plugin validate ."
#
# Starts tests/mock_pve.py on a random port, runs the scripts against it,
# checks the guard rule table and prints PASS/FAIL/SKIP lines plus a summary.
# Exits 1 when any check fails.
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
doctor="scripts/pve-doctor.sh"
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
capture task_bogus "$task" "UPID:pve1:bogus:x" --interval 0.1
assert_exit "pve-task bogus UPID (API 500) exits 2" 2 "$rc"
capture task_forever "$task" "UPID:pve1:000E1F2A:0005F3A4:66F00040:qmforever:100:test@pve!ci:" --timeout 1 --interval 0.2
assert_exit "pve-task running task times out with 4" 4 "$rc"
assert_contains "pve-task timeout names the UPID" "$out" "qmforever:100"

# ---------------------------------------------------------------- (17) doctor
capture doctor_ok "$doctor"
assert_exit "pve-doctor exits 0" 0 "$rc"
assert_contains "pve-doctor prints [ok]" "$out" "[ok]"
assert_contains "pve-doctor prints version" "$out" "9.2.1"
assert_contains "pve-doctor lists pve1" "$out" "pve1"
assert_contains "pve-doctor prints the token id" "$out" "test@pve!ci"
assert_contains "pve-doctor hides the secret" "$out" "set (hidden)"
assert_not_contains "pve-doctor never prints the secret" "$out" "0123-secret"
assert_contains "pve-doctor summarises capability" "$out" "token capability: operator"
capture doctor_wrong_secret env PVE_TOKEN_SECRET=wrong-secret "$doctor"
assert_exit "pve-doctor wrong secret exits 3" 3 "$rc"
capture doctor_bad_host env PVE_HOST=http://127.0.0.1:1 PVE_TIMEOUT=5 "$doctor"
assert_exit "pve-doctor unreachable host exits 2" 2 "$rc"
capture doctor_no_host env -u PVE_HOST "$doctor"
assert_exit "pve-doctor without PVE_HOST exits 1" 1 "$rc"
assert_contains "pve-doctor without PVE_HOST names it" "$out" "PVE_HOST"
capture doctor_help "$doctor" --help
assert_exit "pve-doctor --help exits 0" 0 "$rc"

# ---------------------------------------------------------------- (18) pve-ssh
capture ssh_no_host env -u PVE_HOST -u PVE_SSH_HOST "$sshtool" pveversion
assert_exit "pve-ssh without host exits 1" 1 "$rc"
assert_contains "pve-ssh without host explains" "$out" "no host"
capture ssh_no_cmd env PVE_SSH_HOST=pve1 "$sshtool"
assert_exit "pve-ssh without command exits 1" 1 "$rc"
capture ssh_help "$sshtool" --help
assert_exit "pve-ssh --help exits 0" 0 "$rc"
assert_contains "pve-ssh --help prints usage" "$out" "Usage:"

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
if [ "$elapsed_ms" -lt 2000 ]; then pass "20 guard runs take < 2 s (${elapsed_ms} ms)"; else fail "20 guard runs take < 2 s" "${elapsed_ms} ms"; fi

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
  capture validate claude plugin validate .
  if [ "$rc" -eq 0 ]; then pass "claude plugin validate ."; else fail "claude plugin validate ." "$(printf '%s' "$out" | head -n 20)"; fi
else
  skip "claude plugin validate" "claude CLI not installed"
fi

# ---------------------------------------------------------------- summary
printf '\nsummary: %d passed, %d failed, %d skipped\n' "$passed" "$failed" "$skipped"
if [ "$failed" -gt 0 ]; then
  exit 1
fi
exit 0
