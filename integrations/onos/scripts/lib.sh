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

# The apps the OLS device needs, by what each registers: restsb the REST
# southbound and its `rest` netcfg, optical-model the OCh port model,
# drivers.odtn-driver the `ols` driver, odtn-service the app that pulls them in.
ODTN_APPS="restsb optical-model drivers.odtn-driver odtn-service"

# Whether an app is *running*, not merely marked active.
#
# /applications/<app> reports `state` from ONOS's application store, which says
# ACTIVE as soon as activation is requested -- also while the activation is
# postponed, and while a Karaf bundle refresh has every one of the app's
# components stopped. /applications/<app>/health instead asks
# ComponentsMonitorService.isFullyStarted(app.features()): every bundle of the
# app's features installed and every enabled component in them ACTIVE
# (ApplicationsWebResource.health in web/api, ComponentsMonitorManager in
# core/net, ONOS 2.7). "ready" there is the first point at which, for
# drivers.odtn-driver, the `ols` driver is registered.
onos_app_ready() {
  onos_api GET "/onos/v1/applications/org.onosproject.$1/health" 2>/dev/null | python3 -c "
import json, sys
try:
    sys.exit(0 if json.load(sys.stdin).get('message') == 'ready' else 1)
except Exception:
    sys.exit(1)
"
}

# Ready to take the device's netcfg and bind the `ols` driver to it.
#
# On a cold boot ONOS activates odtn-service's ~20 dependencies one by one, and
# installing protocols.grpc among them triggers a Karaf bundle refresh that
# restarts NetworkConfigManager, DeviceManager and RestDeviceProvider, a minute
# or more after the REST API first answers. A netcfg pushed inside that window
# is refused with HTTP 207 "subjectClassKey 'devices' not found" (the restarted
# NetworkConfigManager has no subject factories until BasicNetworkConfigs
# registers them again); one pushed before it is replayed after the restart,
# reaches RestDeviceProvider before the `ols` driver exists, fails with
# "Driver not found", and is never retried. drivers.odtn-driver requires
# drivers.gnmi, which requires protocols.grpc, so it cannot report ready until
# that refresh is over: waiting for every app here to report ready, and for the
# `devices` config subject to be registered, waits the refresh out.
onos_device_config_ready() {
  onos_ready || return 1
  [ "$(curl -s -o /dev/null -w '%{http_code}' -u "${ONOS_AUTH}" \
        "${ONOS_URL}/onos/v1/network/configuration/devices")" = "200" ] || return 1
  local app
  for app in ${ODTN_APPS}; do onos_app_ready "${app}" || return 1; done
}

onos_device_present() {
  # onos_device_present [--available]
  onos_api GET "/onos/v1/devices" 2>/dev/null | python3 -c "
import json, sys
try:
    devs = json.load(sys.stdin)['devices']
except Exception:
    sys.exit(1)
need_up = sys.argv[2] == '--available'
sys.exit(0 if any(d['id'] == sys.argv[1] and (d['available'] or not need_up)
                  for d in devs) else 1)
" "${DEVICE_ID}" "${1:-}"
}

# HTTP status of the device's entry in ONOS's netcfg: 200 when ONOS holds a
# configuration for it, 404 when it does not.
onos_device_config_code() {
  curl -s -o /dev/null -w '%{http_code}' -u "${ONOS_AUTH}" \
    "${ONOS_URL}/onos/v1/network/configuration/devices/${DEVICE_ID}"
}

# POST the netcfg, retrying with a bounded back-off while ONOS answers with
# something that a restart in progress explains: no answer, a 5xx, a 404 from
# a web container being re-registered, or a 207 naming the `devices` subject.
# Any other answer is a real refusal and fails at once. Idempotent: posting the
# configuration ONOS already holds changes nothing.
onos_push_netcfg() {
  local attempt=1 attempts=8 delay=5 response code
  while :; do
    response="$(netcfg_body | curl -sS -u "${ONOS_AUTH}" -X POST \
      -H 'Content-Type: application/json' --data-binary @- \
      -w '\n%{http_code}' "${ONOS_URL}/onos/v1/network/configuration" 2>&1)" || true
    code="$(printf '%s' "${response}" | tail -1)"
    case "${code}" in
      200) return 0 ;;
      000|404|5??) ;;
      207) case "${response}" in
             *"'devices' not found"*) ;;
             *) printf '%s\n' "netcfg POST returned HTTP 207: ${response%$'\n'*}" >&2; return 1 ;;
           esac ;;
      *) printf '%s\n' "netcfg POST returned HTTP ${code}: ${response%$'\n'*}" >&2; return 1 ;;
    esac
    if [ "${attempt}" -ge "${attempts}" ]; then
      printf '%s\n' "netcfg POST still answered HTTP ${code} after ${attempts} attempts: ${response%$'\n'*}" >&2
      return 1
    fi
    printf '     netcfg POST answered HTTP %s (ONOS restarting?); retry %d/%d in %ss\n' \
      "${code}" "${attempt}" "$((attempts - 1))" "${delay}" >&2
    sleep "${delay}"
    attempt=$((attempt + 1))
    delay=$((delay < 30 ? delay + 5 : 30))
  done
}

# Remove the device's netcfg and wait until ONOS has dropped it. Retries the
# DELETE on the same transient answers as the push.
onos_remove_device_config() {
  local attempt code
  for attempt in 1 2 3 4 5 6; do
    code="$(curl -s -o /dev/null -w '%{http_code}' -u "${ONOS_AUTH}" -X DELETE \
      "${ONOS_URL}/onos/v1/network/configuration/devices/${DEVICE_ID}")"
    case "${code}" in
      200|204) break ;;
    esac
    sleep 5
  done
  wait_for "ONOS to drop the device configuration" 60 \
    bash -c "[ \"\$(curl -s -o /dev/null -w '%{http_code}' -u '${ONOS_AUTH}' \
      '${ONOS_URL}/onos/v1/network/configuration/devices/${DEVICE_ID}')\" = 404 ]"
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

# The id of the REST-app flow rule whose in-port is <port>, if there is one.
# ONOS derives a flow rule's id from its device, priority, selector and app, not
# its treatment, so on the OLS device two lightpaths from one in-port collide:
# the second flow replaces the first in ONOS, and the twin is left holding a
# lightpath ONOS no longer knows about until reconciliation removes it.
onos_flow_on_port() {
  onos_api GET "/onos/v1/flows/${DEVICE_ID}" 2>/dev/null | python3 -c "
import json, sys
try:
    flows = json.load(sys.stdin).get('flows', [])
except Exception:
    sys.exit(0)
for f in flows:
    if f.get('appId') != 'org.onosproject.rest' or f.get('state') == 'PENDING_REMOVE':
        continue
    if any(c.get('type') == 'IN_PORT' and str(c.get('port')) == sys.argv[1]
           for c in f['selector']['criteria']):
        print(f['id']); break
" "$1"
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
