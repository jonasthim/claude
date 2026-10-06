#!/usr/bin/env bash
# pve-api.sh - call the Proxmox VE REST API with an API token.
#
# Usage: pve-api.sh <GET|POST|PUT|DELETE> <path> [key=value ...]
#
# Environment:
#   PVE_HOST          host[:port] (default port 8006) or a full URL with scheme;
#                     a trailing /api2/json or "/" is stripped; IPv6 literals
#                     must be bracketed ([::1]:8006)
#   PVE_TOKEN_ID      USER@REALM!TOKENID
#   PVE_TOKEN_SECRET  token value (never printed)
#   PVE_INSECURE=1    skip TLS verification (opt-in, prints a warning)
#   PVE_CA_CERT       CA bundle passed to curl --cacert
#   PVE_TIMEOUT       curl --max-time in seconds (default 30)
#   PVE_API_RAW=1     print the full response body instead of .data
#   PVE_API_DEBUG=1   print method and URL on stderr (never the auth header)
#   PVE_API_QUIET_TLS=1  suppress the PVE_INSECURE warning (set by pve-task.sh
#                     for its polling calls; direct calls keep the warning)
#   PVE_DRY_RUN=1     print {method,url,params} as JSON on stdout and exit 0
#                     without calling the API
#
# Exit codes: 0 success (2xx); 1 usage, missing env or missing curl/jq;
#             2 transport or TLS failure; 3 HTTP 4xx;
#             4 HTTP 5xx or any other non-2xx/non-4xx status.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: pve-api.sh <GET|POST|PUT|DELETE> <path> [key=value ...]

  path   API path with or without leading "/" or "/api2/json"; may carry ?query
  params split at the first "=": GET/DELETE send them as query parameters,
         POST/PUT send them as a form-encoded body (--data-urlencode)

Env: PVE_HOST PVE_TOKEN_ID PVE_TOKEN_SECRET [PVE_INSECURE=1] [PVE_CA_CERT]
     [PVE_TIMEOUT=30] [PVE_API_RAW=1] [PVE_API_DEBUG=1] [PVE_API_QUIET_TLS=1]
     [PVE_DRY_RUN=1]
     PVE_HOST is host[:port] or a URL; a trailing /api2/json is stripped and
     IPv6 literals must be bracketed ([::1]:8006).

Output: .data of the JSON response (bare string for UPIDs, pretty JSON
        otherwise); PVE_API_RAW=1 prints the whole body.
        PVE_DRY_RUN=1 prints {"method","url","params"} and exits 0; no
        request is made.
Exit:   0 2xx | 1 usage/env/deps | 2 transport/TLS | 3 HTTP 4xx
        4 HTTP 5xx or any other non-2xx/non-4xx status
EOF
}

die_usage() {
  printf 'pve-api: %s\n' "$1" >&2
  printf 'Run pve-api.sh --help for usage.\n' >&2
  exit 1
}

for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
  esac
done

[ "$#" -ge 2 ] || die_usage "need <METHOD> and <path>"

method="${1^^}"
path="$2"
shift 2

case "$method" in
  GET|POST|PUT|DELETE) ;;
  *) die_usage "unsupported method '$method' (use GET, POST, PUT or DELETE)" ;;
esac

command -v curl >/dev/null 2>&1 || die_usage "curl is required but not installed"
command -v jq >/dev/null 2>&1 || die_usage "jq is required but not installed"

for var in PVE_HOST PVE_TOKEN_ID PVE_TOKEN_SECRET; do
  if [ -z "${!var:-}" ]; then
    die_usage "environment variable $var is not set"
  fi
done

# Build the base URL. Strip trailing slashes and a trailing /api2/json first so
# "https://pve:8006/api2/json" does not double the prefix. A value with "://"
# is then used verbatim (useful for mocks); otherwise assume https and add
# :8006 when no port is given. IPv6 literals must be bracketed ([::1]:8006).
base="$PVE_HOST"
while [[ "$base" == */ ]]; do base="${base%/}"; done
base="${base%/api2/json}"
while [[ "$base" == */ ]]; do base="${base%/}"; done
if [[ "$base" != *"://"* ]]; then
  if [[ "$base" =~ :[0-9]+$ ]]; then
    base="https://$base"
  else
    base="https://$base:8006"
  fi
fi


# Normalise the path: strip any /api2/json prefix and ensure a leading slash.
path="${path#/}"
path="${path#api2/json}"
path="${path#/}"
path="/$path"
display_path="$path"
url="$base/api2/json$path"

curl_args=(-sS --max-time "${PVE_TIMEOUT:-30}" -w '\n%{http_code}')
curl_args+=(-H "Authorization: PVEAPIToken=${PVE_TOKEN_ID}=${PVE_TOKEN_SECRET}")

if [ "${PVE_INSECURE:-0}" = "1" ]; then
  if [ "${PVE_API_QUIET_TLS:-0}" != "1" ]; then
    printf 'pve-api: warning: PVE_INSECURE=1, TLS certificate verification is disabled\n' >&2
  fi
  curl_args+=(-k)

elif [ -n "${PVE_CA_CERT:-}" ]; then
  curl_args+=(--cacert "$PVE_CA_CERT")
fi

case "$method" in
  GET)
    # Parameters go to the query string; never send a body on GET.
    [ "$#" -gt 0 ] && curl_args+=(-G)
    ;;
  DELETE)
    curl_args+=(-X DELETE)
    [ "$#" -gt 0 ] && curl_args+=(-G)
    ;;
  POST|PUT)
    # With no parameters curl sends the method without a body.
    curl_args+=(-X "$method")
    ;;
esac

for kv in "$@"; do
  if [[ "$kv" != *"="* ]]; then
    die_usage "parameter '$kv' is not of the form key=value"
  fi
  curl_args+=(--data-urlencode "$kv")
done

curl_args+=("$url")

if [ "${PVE_API_DEBUG:-0}" = "1" ]; then
  printf 'pve-api: %s %s\n' "$method" "$url" >&2
fi

if [ "${PVE_DRY_RUN:-0}" = "1" ]; then
  # Dry run: show exactly what would be sent, then stop before any network access.
  # "$@" still holds the raw key=value parameters (validated above).
  jq -n --arg method "$method" --arg url "$url" \
    '{method: $method, url: $url, params: ($ARGS.positional | map(index("=") as $i | {key: .[:$i], value: .[$i+1:]}) | from_entries)}' \
    --args "$@"
  exit 0
fi

errfile="$(mktemp)"
trap 'rm -f "$errfile"' EXIT

set +e
out="$(curl "${curl_args[@]}" 2>"$errfile")"
rc=$?
set -e

if [ "$rc" -ne 0 ]; then
  errmsg="$(tr -d '\r' <"$errfile" | sed -e 's/^curl: //' | tr '\n' ' ')"
  printf 'pve-api: curl failed (%s): %s\n' "$rc" "${errmsg% }" >&2
  exit 2
fi

code="${out##*$'\n'}"
body="${out%$'\n'*}"
if [ "$out" = "$code" ]; then
  # No newline in the output means an empty body.
  body=""
fi

is_json=0
if [ -n "$body" ] && printf '%s' "$body" | jq -e . >/dev/null 2>&1; then
  is_json=1
fi

case "$code" in
  2??)
    if [ "${PVE_API_RAW:-0}" = "1" ] || [ "$is_json" -eq 0 ]; then
      printf '%s\n' "$body"
    else
      printf '%s' "$body" | jq -r '.data'
    fi
    exit 0
    ;;
  4??) exit_code=3 ;;
  *)   exit_code=4 ;;
esac

# Error path: print the server message and per-parameter errors on stderr.
message=""
if [ "$is_json" -eq 1 ]; then
  message="$(printf '%s' "$body" | jq -r '.message // empty' | tr -d '\r' | sed -e 's/[[:space:]]*$//')"
fi
if [ -z "$message" ]; then
  message="$(printf '%s' "$body" | tr '\n' ' ' | cut -c1-300)"
fi
printf 'pve-api: HTTP %s %s %s: %s\n' "$code" "$method" "$display_path" "$message" >&2
if [ "$is_json" -eq 1 ]; then
  printf '%s' "$body" \
    | jq -r '(.errors // {}) | to_entries[] | "  \(.key): \(.value)"' >&2 || true
fi
exit "$exit_code"
