#!/bin/bash
# Start a twin for the browser tests: playwright.config.ts runs this through
# its webServer setting, once per twin, and stops it after the run.
#
#   start.sh <config.yaml> <rest-port> <grpc-port> <state-dir>
#
# The twinlight executable is $TWINLIGHT_BIN if set, else the repository's
# .venv, else the one on PATH. The GNPy reference files are not in git, so
# they are fetched first when missing (CLAUDE.md, hard constraint 6).
# TWINLIGHT_CORS_ORIGINS and TWINLIGHT_API_TOKENS come from the environment
# playwright.config.ts gives this script.
set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: $0 <config.yaml> <rest-port> <grpc-port> <state-dir>" >&2
  exit 2
fi
rest_port=$2 grpc_port=$3
# Absolute, since the twin runs from the repository root.
config=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
mkdir -p "$4"
state_dir=$(cd "$4" && pwd)

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)

if [ -n "${TWINLIGHT_BIN:-}" ]; then
  twinlight=$TWINLIGHT_BIN
elif [ -x "$repo/.venv/bin/twinlight" ]; then
  twinlight=$repo/.venv/bin/twinlight
elif command -v twinlight >/dev/null 2>&1; then
  twinlight=$(command -v twinlight)
else
  echo "twinlight not found: set TWINLIGHT_BIN, or install the backend into $repo/.venv" >&2
  exit 1
fi

if [ ! -f "$repo/examples/gnpy-data/eqpt_config.json" ] ||
   [ ! -f "$repo/examples/gnpy-data/CORONET_CONUS_Topology.json" ]; then
  # twinlight-fetch-examples is installed next to twinlight.
  (cd "$repo" && "$(dirname "$twinlight")/twinlight-fetch-examples")
fi

cd "$repo"
exec "$twinlight" \
  --config "$config" \
  --rest-host 127.0.0.1 \
  --rest-port "$rest_port" \
  --grpc-port "$grpc_port" \
  --snapshot-dir "$state_dir" \
  --env-file "$here/no-secrets.env" \
  --reset
