#!/usr/bin/env bash
# Create a private virtualenv for the truenas skill and install the TrueNAS API client into it.
# tn.py re-executes itself inside this venv automatically, so you only run this once.
#
#   TRUENAS_VENV   override the venv location (default: ~/.cache/truenas-skill/venv)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${TRUENAS_VENV:-$HOME/.cache/truenas-skill/venv}"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required: the TrueNAS API client is installed from GitHub." >&2
  exit 1
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating virtualenv at $VENV"
  python3 -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"
"$VENV/bin/python" - <<'PY'
import truenas_api_client, inspect
sig = inspect.signature(truenas_api_client.Client.__init__)
print("truenas_api_client installed; verify_ssl supported:", "verify_ssl" in sig.parameters)
PY
echo "Done. midclt is also available at $VENV/bin/midclt"
