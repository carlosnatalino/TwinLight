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

# The stack's names, ports and subnet come from the same variables the two
# compose files interpolate (README § Running a second stack side by side).
# Compose reads them from the environment and then from the repo-root .env, so
# the scripts do the same, or a port moved in .env would move the containers
# and leave the scripts knocking on the old one. Only these names are read
# from .env; the environment always wins.
_tl_load_dotenv() {
  local file="${REPO_ROOT}/.env" line name value
  [ -f "${file}" ] || return 0
  while IFS= read -r line || [ -n "${line}" ]; do
    case "${line}" in
      COMPOSE_PROJECT_NAME=*|TWINLIGHT_CONTAINER_PREFIX=*|TWINLIGHT_IMAGE_TAG=*|\
      TWINLIGHT_*_PORT=*|TWINLIGHT_ONOS_SUBNET_PREFIX=*|TWINLIGHT_SNAPSHOT_DIR=*|\
      TWINLIGHT_COMPOSE_FILES=*) ;;
      *) continue ;;
    esac
    name="${line%%=*}"; value="${line#*=}"
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
    # Set only what the environment does not already set.
    if [ -z "${!name+x}" ]; then export "${name}=${value}"; fi
  done < "${file}"
}
_tl_load_dotenv

TL_PREFIX="${TWINLIGHT_CONTAINER_PREFIX:-twinlight}"
TWIN_CONTAINER="${TL_PREFIX}"
ADAPTER_CONTAINER="${TL_PREFIX}-tapi-adapter"
ONOS_CONTAINER="${TL_PREFIX}-onos"

ONOS_URL="${ONOS_URL:-http://localhost:${TWINLIGHT_ONOS_REST_PORT:-8181}}"
ONOS_AUTH="${ONOS_AUTH:-onos:rocks}"
ADAPTER_URL="${ADAPTER_URL:-http://localhost:${TWINLIGHT_ADAPTER_PORT:-8282}}"
TWIN_URL="${TWIN_URL:-http://localhost:${TWINLIGHT_REST_PORT:-8080}}"
UI_URL="http://localhost:${TWINLIGHT_UI_PORT:-5173}"
GRAFANA_URL="http://localhost:${TWINLIGHT_GRAFANA_PORT:-3000}"

# Must match the static IP and port in docker-compose.onos.yml / netcfg.
DEVICE_ID="${DEVICE_ID:-rest:${TWINLIGHT_ONOS_SUBNET_PREFIX:-172.28}.0.10:8282}"

COMPOSE_FILES=(-f "${REPO_ROOT}/docker-compose.yml" -f "${ONOS_DIR}/docker-compose.onos.yml")
# Further files, colon-separated, relative to the repo root or absolute; they
# go last so they override the two above.
if [ -n "${TWINLIGHT_COMPOSE_FILES:-}" ]; then
  _tl_old_ifs="${IFS}"; IFS=':'
  for _tl_file in ${TWINLIGHT_COMPOSE_FILES}; do
    [ -n "${_tl_file}" ] && COMPOSE_FILES+=(-f "${_tl_file}")
  done
  IFS="${_tl_old_ifs}"; unset _tl_old_ifs _tl_file
fi

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

# Compose takes the project name from COMPOSE_PROJECT_NAME (environment or
# .env) by itself; without one it is the repo directory's name.
compose() { ( cd "${REPO_ROOT}" && docker compose "${COMPOSE_FILES[@]}" "$@" ); }

# The netcfg document for DEVICE_ID. netcfg/twinlight-ols.json is written for
# the default subnet; for any other DEVICE_ID the device key and the rest
# block's ip/port are rewritten to match, so the checked-in file stays the one
# source of every other field.
netcfg_body() {
  python3 - "${ONOS_DIR}/netcfg/twinlight-ols.json" "${DEVICE_ID}" <<'PY'
import json, sys
path, device_id = sys.argv[1], sys.argv[2]
with open(path) as f:
    raw = f.read()
cfg = json.loads(raw)
(key, entry), = cfg["devices"].items()
if key == device_id:
    sys.stdout.write(raw)
    raise SystemExit
proto, ip, port = device_id.split(":")
if proto != "rest":
    sys.exit("DEVICE_ID must look like rest:<ip>:<port>, got %r" % device_id)
entry["rest"]["ip"], entry["rest"]["port"] = ip, int(port)
print(json.dumps({"devices": {device_id: entry}}, indent=2))
PY
}

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
  onos_ready \
    || die "ONOS not ready at ${ONOS_URL} — run integrations/onos/scripts/demo-up.sh first"
}
