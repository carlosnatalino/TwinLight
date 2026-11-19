#!/usr/bin/env bash
# Shared helpers for the TwinLight + ONOS demo scripts.
# Sourced, not executed. POSIX-ish bash 3.2 so it works with macOS's stock shell.

set -euo pipefail

# Repo root, regardless of where the caller invoked the script from.
ONOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# integrations/onos/scripts -> integrations/onos -> integrations -> repo root.
REPO_ROOT="$(cd "${ONOS_DIR}/../.." && pwd)"
# Where tapi_fields.py and find_pairs.py live, for the embedded python3 -c
# snippets that import them.
SCRIPT_DIR="${ONOS_DIR}/scripts"

ONOS_URL="${ONOS_URL:-http://localhost:8181}"
ONOS_AUTH="${ONOS_AUTH:-onos:rocks}"
ADAPTER_URL="${ADAPTER_URL:-http://localhost:8282}"
TWIN_URL="${TWIN_URL:-http://localhost:8080}"

# Must match the static IP and port in docker-compose.onos.yml / netcfg.
DEVICE_ID="${DEVICE_ID:-rest:172.28.0.10:8282}"

# The token these scripts present to a twin that requires one: the same
# TWINLIGHT_API_TOKEN the CLIs and the adapter read, from the environment or
# else from the repository's .env (where Compose reads it for the adapter).
# Exported for the Python helpers; never printed. twin_curl and
# tapi_fields.twin_urlopen send it to the twin only, never to ONOS or the
# adapter -- neither needs it.
if [ -z "${TWINLIGHT_API_TOKEN:-}" ] && [ -f "${REPO_ROOT}/.env" ]; then
  TWINLIGHT_API_TOKEN="$(awk '
    /^[[:space:]]*(export[[:space:]]+)?TWINLIGHT_API_TOKEN[[:space:]]*=/ {
      v = $0
      sub(/\r$/, "", v); sub(/^[^=]*=[[:space:]]*/, "", v)
      sub(/[[:space:]]+#.*$/, "", v); sub(/[[:space:]]+$/, "", v)
      gsub(/^["\047]|["\047]$/, "", v)
      token = v
    }
    END { print token }' "${REPO_ROOT}/.env")"
fi
if [ -n "${TWINLIGHT_API_TOKEN:-}" ]; then
  export TWINLIGHT_API_TOKEN
fi

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

# The twin's T-API data resource as a URL, {TWIN_URL}{root}/data, with the
# RESTCONF root read from its /.well-known/host-meta (/data when it serves
# none). Call it once per script, when the twin is up, and keep the result.
twin_data() {
  python3 -c "import sys; sys.path.insert(0, sys.argv[1]); from tapi_fields import twin_data_root; print(twin_data_root(sys.argv[2]))" \
    "${SCRIPT_DIR}" "${TWIN_URL}"
}

# curl for requests to the twin: the same arguments, plus the bearer token when
# one is set. The header is passed through a file descriptor rather than the
# command line, so the token does not show in the process list. For ONOS and
# the adapter use plain curl.
twin_curl() {
  if [ -n "${TWINLIGHT_API_TOKEN:-}" ]; then
    curl -H @<(printf 'Authorization: Bearer %s\n' "${TWINLIGHT_API_TOKEN}") "$@"
  else
    curl "$@"
  fi
}

# Why the twin refuses this shell's token (missing or not accepted), printed;
# fails then, succeeds silently otherwise -- also when the twin is down, which
# is not an authorization problem.
twin_auth_problem() {
  python3 -c "import sys; sys.path.insert(0, sys.argv[1]); from tapi_fields import twin_auth_problem; p = twin_auth_problem(sys.argv[2]); print(p or ''); sys.exit(1 if p else 0)" \
    "${SCRIPT_DIR}" "${TWIN_URL}"
}

# Why the twin refuses the ADAPTER's token, as the adapter's /health reports
# it; fails then, succeeds silently otherwise. Without this, a refused adapter
# looks like ONOS discovering zero ports, or a lightpath that never appears.
adapter_auth_problem() {
  local health
  health="$(curl -sS "${ADAPTER_URL}/health" 2>/dev/null)" || return 0
  printf '%s' "${health}" | python3 -c '
import json, sys
try:
    health = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
if health.get("twin-authorized") is False:
    print(health.get("twin-auth-error") or "the twin refuses the adapter")
    sys.exit(1)
'
}

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
  # Tolerate a short startup window: these scripts are often run immediately
  # after demo-up.sh or a `compose up --build` of the adapter, where the
  # container is up but uvicorn has not bound yet. Anything longer than this is
  # a real problem worth reporting rather than waiting on.
  local waited=0
  until curl -sSf "${ADAPTER_URL}/health" >/dev/null 2>&1; do
    [ "${waited}" -lt 30 ] \
      || die "adapter not reachable at ${ADAPTER_URL} — run integrations/onos/scripts/demo-up.sh first"
    sleep 3
    waited=$((waited + 3))
  done
  local why
  why="$(adapter_auth_problem)" \
    || die "adapter: ${why} — see integrations/onos/README.md § Authentication"
  onos_ready \
    || die "ONOS not ready at ${ONOS_URL} — run integrations/onos/scripts/demo-up.sh first"
  why="$(twin_auth_problem)" || die "${why}"
}
