#!/usr/bin/env bash
# Shared helpers for the TwinLight + ONOS demo scripts.
# Sourced, not executed. POSIX-ish bash 3.2 so it works with macOS's stock shell.

set -euo pipefail

# Repo root, regardless of where the caller invoked the script from.
ONOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${ONOS_DIR}/.." && pwd)"

ONOS_URL="${ONOS_URL:-http://localhost:8181}"
ONOS_AUTH="${ONOS_AUTH:-onos:rocks}"
ADAPTER_URL="${ADAPTER_URL:-http://localhost:8282}"
TWIN_URL="${TWIN_URL:-http://localhost:8080}"

# Must match the static IP and port in docker-compose.onos.yml / netcfg.
DEVICE_ID="${DEVICE_ID:-rest:172.28.0.10:8282}"

COMPOSE_FILES=(-f "${REPO_ROOT}/docker-compose.yml" -f "${ONOS_DIR}/docker-compose.onos.yml")

if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'
else
  C_RESET=''; C_BOLD=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''
fi

say()  { printf '%s==>%s %s\n' "${C_BLUE}${C_BOLD}" "${C_RESET}" "$*"; }
ok()   { printf '%s  OK%s %s\n' "${C_GREEN}" "${C_RESET}" "$*"; }
warn() { printf '%s WARN%s %s\n' "${C_YELLOW}" "${C_RESET}" "$*"; }
die()  { printf '%sFAIL%s %s\n' "${C_RED}${C_BOLD}" "${C_RESET}" "$*" >&2; exit 1; }

compose() { ( cd "${REPO_ROOT}" && docker compose "${COMPOSE_FILES[@]}" "$@" ); }

onos_api() {
  # onos_api <method> <path> [json-body]
  local method="$1" path="$2" body="${3:-}"
  if [ -n "${body}" ]; then
    curl -sS -u "${ONOS_AUTH}" -X "${method}" \
      -H 'Content-Type: application/json' -d "${body}" "${ONOS_URL}${path}"
  else
    curl -sS -u "${ONOS_AUTH}" -X "${method}" "${ONOS_URL}${path}"
  fi
}

# Readiness, not reachability. ONOS answers on :8181 long before its core
# services register: for the first minute or two of Karaf boot every endpoint
# returns a JSON 503 body ("Service org.onosproject.net.device.DeviceService
# not found") with HTTP 503. A plain curl treats that as a successful request,
# so readiness has to be judged on the payload.
onos_ready() {
  onos_api GET /onos/v1/devices 2>/dev/null | python3 -c "
import json, sys
try:
    sys.exit(0 if 'devices' in json.load(sys.stdin) else 1)
except Exception:
    sys.exit(1)
"
}

onos_app_active() {
  onos_api GET "/onos/v1/applications/org.onosproject.$1" 2>/dev/null | python3 -c "
import json, sys
try:
    sys.exit(0 if json.load(sys.stdin).get('state') == 'ACTIVE' else 1)
except Exception:
    sys.exit(1)
"
}

# Resolve a transceiver city name (e.g. "Atlanta") to the ONOS port number the
# adapter assigned to its SIP. Matching is case-insensitive and substring-based
# so "atlanta" finds "trx Atlanta".
port_for_node() {
  local needle="$1"
  curl -sS "${ADAPTER_URL}/adapter/ports" | python3 -c "
import json, sys
needle = sys.argv[1].lower()
ports = json.load(sys.stdin)['ports']
hits = [p for p in ports if needle in (p['node-name'] or '').lower()]
if not hits:
    sys.exit('no transceiver matching %r' % sys.argv[1])
if len(hits) > 1:
    exact = [p for p in hits if (p['node-name'] or '').lower() == 'trx ' + needle]
    if len(exact) != 1:
        sys.exit('ambiguous %r: %s' % (sys.argv[1], ', '.join(p['node-name'] for p in hits)))
    hits = exact
print(hits[0]['onos-port'])
" "${needle}"
}

wait_for() {
  # wait_for <label> <timeout-seconds> <command...>
  local label="$1" timeout="$2"; shift 2
  local waited=0
  printf '     waiting for %s ' "${label}"
  while ! "$@" >/dev/null 2>&1; do
    if [ "${waited}" -ge "${timeout}" ]; then
      printf '\n'; return 1
    fi
    printf '.'
    sleep 5
    waited=$((waited + 5))
  done
  printf ' (%ss)\n' "${waited}"
  return 0
}

require_stack() {
  curl -sSf "${ADAPTER_URL}/health" >/dev/null 2>&1 \
    || die "adapter not reachable at ${ADAPTER_URL} — run onos/scripts/demo-up.sh first"
  onos_ready \
    || die "ONOS not ready at ${ONOS_URL} — run onos/scripts/demo-up.sh first"
}
