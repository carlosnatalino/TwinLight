#!/usr/bin/env bash
# Test the TwinLight <-> ONOS integration on this machine.
#
#   test-integration.sh unit [--pinned]       the adapter's unit tests; no Docker
#                                             unless --pinned
#   test-integration.sh e2e [options]         cold boot, demo-up.sh, validate.sh,
#                                             a pass over every other script,
#                                             teardown
#   test-integration.sh all [options]         unit, then e2e
#   test-integration.sh preflight             only check that the test stack has
#                                             room to boot; changes nothing
#
# Authentication: export TWINLIGHT_API_TOKENS (the twin's tokens) and
# TWINLIGHT_API_TOKEN (one of them, for the adapter and the scripts) before
# running, and the whole stack runs with tokens on; an extra step checks that
# the twin refuses an anonymous request. Unset, authentication is off.
#
# Seeding draws its pairs with SEED_RANDOM_SEED, `twinlight-onos-test` unless
# exported; the run prints the seed, so a failure can be replayed with it.
# ONOS_TEST_QOT_MARGIN_DB, if exported, is the twin's admission margin for the
# main seeding (restored afterwards), to test against a margin other than 1.5.
#
# e2e options:
#   --keep        leave the stack up afterwards, for inspection
#   --reuse       test a stack that is already up instead of booting one; it is
#                 never torn down, whoever started it
#   --boots N     cold-boot N times (down -v, demo-up.sh, timed) before the
#                 checks run on the last one; default 1
#   --rmi         also remove the images this run built, at teardown
#   --out DIR     where logs go (default: a new directory under $TMPDIR)
#
# The test stack has its own identity, so it runs beside any other stack:
# project, container prefix and image tag `twinlight-onos-test`, host ports
# 28080 (REST), 25051, 25173, 29090, 23000, 28282 (adapter), 28181 (ONOS),
# 28101, subnet 172.30.0.0/16, and a fresh snapshot directory per run. Each
# comes from the variables in README § Running a second stack side by side,
# read from the environment only: a repo-root .env written for your everyday
# stack is not allowed to point the test at it.
#
# It never touches a stack it did not start. Everything it creates carries the
# label org.twinlight.onos-test, and before a cold boot it refuses to go on if
# the project, the container names, the image tags, the ports or the subnet are
# held by anything without that label.
#
# Exit status is non-zero if anything failed; a summary names each step, and
# every step's output plus the container logs are kept in the run directory.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../../.." && pwd)"
ADAPTER_DIR="${HERE}/../adapter"
LABEL="org.twinlight.onos-test"

usage() { sed -n '2,47p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

MODE="${1:-}"; [ -n "${MODE}" ] || usage; shift
PINNED=""; KEEP=""; REUSE=""; BOOTS=1; RMI=""; OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pinned) PINNED=yes ;;
    --keep)   KEEP=yes ;;
    --reuse)  REUSE=yes ;;
    --rmi)    RMI=yes ;;
    --boots)  shift; BOOTS="${1:-}"; case "${BOOTS}" in ''|*[!0-9]*|0) usage ;; esac ;;
    --out)    shift; OUT="${1:-}"; [ -n "${OUT}" ] || usage ;;
    *)        usage ;;
  esac
  shift
done
case "${MODE}" in unit|e2e|all|preflight) ;; *) usage ;; esac

# --- the test stack's identity ------------------------------------------------
# Exported before lib.sh is sourced: its .env loader only fills names that are
# still unset, so none of these can come from .env.
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-twinlight-onos-test}"
export TWINLIGHT_CONTAINER_PREFIX="${TWINLIGHT_CONTAINER_PREFIX:-twinlight-onos-test}"
export TWINLIGHT_IMAGE_TAG="${TWINLIGHT_IMAGE_TAG:-onos-test}"
export TWINLIGHT_REST_PORT="${TWINLIGHT_REST_PORT:-28080}"
export TWINLIGHT_GNMI_PORT="${TWINLIGHT_GNMI_PORT:-25051}"
export TWINLIGHT_UI_PORT="${TWINLIGHT_UI_PORT:-25173}"
export TWINLIGHT_PROMETHEUS_PORT="${TWINLIGHT_PROMETHEUS_PORT:-29090}"
export TWINLIGHT_GRAFANA_PORT="${TWINLIGHT_GRAFANA_PORT:-23000}"
export TWINLIGHT_ADAPTER_PORT="${TWINLIGHT_ADAPTER_PORT:-28282}"
export TWINLIGHT_ONOS_REST_PORT="${TWINLIGHT_ONOS_REST_PORT:-28181}"
export TWINLIGHT_ONOS_CLI_PORT="${TWINLIGHT_ONOS_CLI_PORT:-28101}"
export TWINLIGHT_ONOS_SUBNET_PREFIX="${TWINLIGHT_ONOS_SUBNET_PREFIX:-172.30}"
# The URLs and the device id follow the variables above; an inherited value
# would point the checks at some other stack.
unset ONOS_URL ADAPTER_URL TWIN_URL DEVICE_ID
# The order seed-demo.sh draws its pairs in (see find_pairs.py).
export SEED_RANDOM_SEED="${SEED_RANDOM_SEED:-twinlight-onos-test}"

RUN_DIR="${OUT:-${TMPDIR:-/tmp}/twinlight-onos-test-$(date +%Y%m%d-%H%M%S)}"
RUN_DIR="${RUN_DIR%/}"
mkdir -p "${RUN_DIR}/steps" "${RUN_DIR}/logs" || { echo "cannot create ${RUN_DIR}" >&2; exit 2; }
export TWINLIGHT_SNAPSHOT_DIR="${TWINLIGHT_SNAPSHOT_DIR:-${RUN_DIR}/snapshots}"

# Every container, volume, network and image of the test stack gets the label.
cat > "${RUN_DIR}/ownership.yml" <<EOF
# Generated by test-integration.sh: marks what this test run creates.
services:
EOF
for svc in twin ui prometheus grafana grafana-init tapi-adapter onos; do
  case "${svc}" in
    twin|ui|tapi-adapter) build="    build:
      labels: {${LABEL}: \"true\"}
" ;;
    *) build="" ;;
  esac
  printf '  %s:\n    labels: {%s: "true"}\n    restart: "no"\n%s' \
    "${svc}" "${LABEL}" "${build}" >> "${RUN_DIR}/ownership.yml"
done
cat >> "${RUN_DIR}/ownership.yml" <<EOF
volumes:
  prometheus-data: {labels: {${LABEL}: "true"}}
  grafana-data: {labels: {${LABEL}: "true"}}
networks:
  default: {labels: {${LABEL}: "true"}}
EOF
export TWINLIGHT_COMPOSE_FILES="${RUN_DIR}/ownership.yml${TWINLIGHT_COMPOSE_FILES:+:${TWINLIGHT_COMPOSE_FILES}}"

# shellcheck source=lib.sh
. "${HERE}/lib.sh"
set +e   # lib.sh turns errexit on; this script records failures instead

# --- bookkeeping --------------------------------------------------------------
STEP_NAMES=(); STEP_RESULTS=(); STEP_SECS=()
STEP_N=0

# step <name> <command...>: run, keep its output, record the outcome.
step() {
  local name="$1"; shift
  STEP_N=$((STEP_N + 1))
  local slug log t0 rc
  slug="$(printf '%02d-%s' "${STEP_N}" "${name}" | tr -c 'A-Za-z0-9._-' '-' | sed 's/-*$//')"
  log="${RUN_DIR}/steps/${slug}.log"
  printf '%s==>%s %-58s ' "${C_BLUE}${C_BOLD}" "${C_RESET}" "${name}"
  t0=$(date +%s)
  ( "$@" ) > "${log}" 2>&1
  rc=$?
  local secs=$(( $(date +%s) - t0 ))
  STEP_NAMES+=("${name}"); STEP_SECS+=("${secs}")
  if [ "${rc}" -eq 0 ]; then
    STEP_RESULTS+=("PASS"); printf '%sPASS%s %4ss\n' "${C_GREEN}" "${C_RESET}" "${secs}"
  else
    STEP_RESULTS+=("FAIL"); printf '%sFAIL%s %4ss  (%s)\n' "${C_RED}${C_BOLD}" "${C_RESET}" "${secs}" "${log}"
    tail -5 "${log}" | sed 's/^/       /'
  fi
  return "${rc}"
}

failures() {
  local i n=0
  for i in "${!STEP_RESULTS[@]}"; do [ "${STEP_RESULTS[$i]}" = PASS ] || n=$((n + 1)); done
  printf '%s' "${n}"
}

summary() {
  local i
  printf '\n%sSummary%s  (%s)\n' "${C_BOLD}" "${C_RESET}" "${RUN_DIR}"
  for i in "${!STEP_NAMES[@]}"; do
    printf '  %-4s %5ss  %s\n' "${STEP_RESULTS[$i]}" "${STEP_SECS[$i]}" "${STEP_NAMES[$i]}"
  done | tee "${RUN_DIR}/summary.txt"
  local n; n="$(failures)"
  if [ "${n}" -eq 0 ]; then
    printf '\n%sall %d steps passed%s\n' "${C_GREEN}${C_BOLD}" "${#STEP_NAMES[@]}" "${C_RESET}"
  else
    printf '\n%s%d of %d steps failed:%s\n' "${C_RED}${C_BOLD}" "${n}" "${#STEP_NAMES[@]}" "${C_RESET}"
    for i in "${!STEP_NAMES[@]}"; do
      [ "${STEP_RESULTS[$i]}" = PASS ] || printf '  - %s\n' "${STEP_NAMES[$i]}"
    done
  fi
  printf 'step outputs: %s/steps   container logs: %s/logs\n' "${RUN_DIR}" "${RUN_DIR}"
}

# --- unit ---------------------------------------------------------------------
# The repo's environment by default: it is the one CONTRIBUTING.md sets up,
# needs no Docker and runs in seconds. Its FastAPI is whatever the twin's
# dependencies resolve to, not the adapter image's pin, so --pinned runs the
# same tests in python:3.12-slim with exactly adapter/requirements.txt.
unit_tests() {
  if [ -n "${PINNED}" ]; then
    docker run --rm -v "${ADAPTER_DIR}:/src:ro" -w /src python:3.12-slim sh -c \
      "pip install -q --disable-pip-version-check --root-user-action=ignore -r requirements.txt pytest \
       && python -m pytest -q -p no:cacheprovider tests"
    return
  fi
  local py="${PYTHON:-}"
  if [ -z "${py}" ]; then
    if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then py="${REPO_ROOT}/.venv/bin/python"; else py=python3; fi
  fi
  if ! "${py}" -c 'import fastapi, httpx, pytest, uvicorn' 2>/dev/null; then
    cat <<EOF
${py} cannot import what the adapter tests need (fastapi, httpx, uvicorn, pytest).
Install the repo's development environment, which provides all four:

  uv venv --python 3.12 && uv pip install -e ".[dev]"

or set PYTHON to an interpreter that has them, or run this with --pinned to
test in a container with exactly integrations/onos/adapter/requirements.txt.
EOF
    return 1
  fi
  "${py}" -c 'import fastapi; print("fastapi", fastapi.__version__, "(adapter image pins",
              open("'"${ADAPTER_DIR}"'/requirements.txt").read().split("fastapi==")[1].split()[0] + ")")'
  ( cd "${REPO_ROOT}" && env -u VIRTUAL_ENV "${py}" -m pytest -q --no-cov -p no:cacheprovider \
      integrations/onos/adapter/tests )
}

# --- e2e preflight: never touch what this run did not create ------------------
project_objects() {
  # project_objects <container|volume|network> -> "name<TAB>label" per object
  local kind="$1" fmt
  case "${kind}" in
    container) docker ps -a --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
                 --format "{{.Names}}	{{.Label \"${LABEL}\"}}" ;;
    volume)    docker volume ls --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
                 --format "{{.Name}}	{{.Label \"${LABEL}\"}}" ;;
    network)   docker network ls --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
                 --format "{{.Name}}	{{.Label \"${LABEL}\"}}" ;;
  esac
}

preflight() {
  local bad="" kind line name
  docker info >/dev/null 2>&1 || { echo "docker is not reachable"; return 1; }
  for kind in container volume network; do
    while IFS='	' read -r name line; do
      [ -n "${name}" ] || continue
      [ "${line}" = "true" ] || bad="${bad}  ${kind} ${name} belongs to project ${COMPOSE_PROJECT_NAME} but not to a test run\n"
    done <<EOF
$(project_objects "${kind}")
EOF
  done
  for name in "${TL_PREFIX}" "${TL_PREFIX}-ui" "${TL_PREFIX}-prometheus" "${TL_PREFIX}-grafana" \
              "${TL_PREFIX}-grafana-init" "${TL_PREFIX}-tapi-adapter" "${TL_PREFIX}-onos"; do
    line="$(docker inspect -f "{{index .Config.Labels \"com.docker.compose.project\"}}" "${name}" 2>/dev/null || true)"
    [ -z "${line}" ] || [ "${line}" = "${COMPOSE_PROJECT_NAME}" ] \
      || bad="${bad}  container name ${name} is taken by project ${line}\n"
  done
  for name in twinlight twinlight-ui twinlight-tapi-adapter; do
    line="$(docker image inspect -f "{{index .Config.Labels \"${LABEL}\"}}" "${name}:${TWINLIGHT_IMAGE_TAG}" 2>/dev/null)" \
      || continue
    [ "${line}" = "true" ] || bad="${bad}  image ${name}:${TWINLIGHT_IMAGE_TAG} exists and was not built by a test run\n"
  done
  # Ports: anything answering a TCP connect is someone else's (our own project
  # has no containers left by now, or it was refused above).
  for name in TWINLIGHT_REST_PORT TWINLIGHT_GNMI_PORT TWINLIGHT_UI_PORT TWINLIGHT_PROMETHEUS_PORT \
              TWINLIGHT_GRAFANA_PORT TWINLIGHT_ADAPTER_PORT TWINLIGHT_ONOS_REST_PORT TWINLIGHT_ONOS_CLI_PORT; do
    if python3 -c 'import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)' "${!name}"; then
      bad="${bad}  port ${!name} (${name}) is in use\n"
    fi
  done
  line="$(docker network ls -q | xargs docker network inspect \
            -f '{{.Name}} {{index .Labels "com.docker.compose.project"}} {{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null \
          | python3 -c '
import ipaddress, sys
mine = ipaddress.ip_network(sys.argv[1] + ".0.0/16")
for row in sys.stdin:
    # "<name> <project or empty> <subnet>..."; a network outside any project
    # has an empty middle field, so split on the first two single spaces.
    name, project, subnets = (row.rstrip("\n").split(" ", 2) + ["", ""])[:3]
    for s in subnets.split():
        try:
            net = ipaddress.ip_network(s)
        except ValueError:
            continue
        if net.version == 4 and net.overlaps(mine) and project != sys.argv[2]:
            print("subnet %s overlaps network %s (%s)" % (mine, name, net))
' "${TWINLIGHT_ONOS_SUBNET_PREFIX}" "${COMPOSE_PROJECT_NAME}")"
  [ -z "${line}" ] || bad="${bad}  ${line}\n"
  if [ -n "${bad}" ]; then
    printf 'refusing to start: this would touch things a test run did not create\n%b' "${bad}"
    printf 'Move the test stack with the variables in README § Running a second stack side by side.\n'
    return 1
  fi
  echo "project ${COMPOSE_PROJECT_NAME}: nothing in the way"
}

reuse_preflight() {
  onos_ready && curl -sSf "${ADAPTER_URL}/health" >/dev/null && curl -sSf "${TWIN_URL}/health" >/dev/null \
    || { echo "--reuse: no running stack answers at ${TWIN_URL}, ${ADAPTER_URL}, ${ONOS_URL}"; return 1; }
  echo "reusing the stack at ${TWIN_URL} / ${ADAPTER_URL} / ${ONOS_URL}"
}

# --- e2e checks -----------------------------------------------------------------
STARTED=""   # set once this run has created containers

cold_boot() {
  compose down -v --remove-orphans || return 1
  mkdir -p "${TWINLIGHT_SNAPSHOT_DIR}" && chmod 777 "${TWINLIGHT_SNAPSHOT_DIR}"
  rm -f "${TWINLIGHT_SNAPSHOT_DIR}"/checkpoint.json*
  "${HERE}/demo-up.sh"
}

adapter_service_count() {
  curl -sS "${ADAPTER_URL}/adapter/status" \
    | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["onos-created-services"]))'
}
onos_rest_flows() {
  # one "flow-id in-port out-port state" line per flow pushed through the REST app
  onos_api GET "/onos/v1/flows/${DEVICE_ID}" | python3 -c '
import json, sys
for f in json.load(sys.stdin).get("flows", []):
    if f.get("appId") != "org.onosproject.rest":
        continue
    i = [c["port"] for c in f["selector"]["criteria"] if c["type"] == "IN_PORT"]
    o = [x["port"] for x in f["treatment"]["instructions"] if x["type"] == "OUTPUT"]
    print(f["id"], i[0] if i else "?", o[0] if o else "?", f["state"])
'
}
until_true() {
  # until_true <seconds> <command...>
  local left="$1"; shift
  until "$@"; do
    [ "${left}" -gt 0 ] || return 1
    sleep 3; left=$((left - 3))
  done
}

SMOKE_A="${SMOKE_A:-Boston}"; SMOKE_Z="${SMOKE_Z:-New_York}"
SMOKE_UUID=""

check_lightpath_create() {
  local out
  out="$("${HERE}/lightpath.sh" create "${SMOKE_A}" "${SMOKE_Z}")" || { printf '%s\n' "${out}"; return 1; }
  printf '%s\n' "${out}"
  printf '%s' "${out}" | grep -q "ADMITTED by the twin" || { echo "not admitted"; return 1; }
  printf '%s' "${out}" | grep -q "GSNR" || { echo "no OPM readback"; return 1; }
}
smoke_uuid() {
  curl -sS "${ADAPTER_URL}/adapter/status" | python3 -c '
import json, sys
eps = json.load(sys.stdin).get("service-endpoints", {})
want = (sys.argv[1].lower(), sys.argv[2].lower())
for uuid, ep in eps.items():
    if ((ep.get("a-end") or "").lower(), (ep.get("z-end") or "").lower()) == want:
        print(uuid); break
' "${SMOKE_A}" "${SMOKE_Z}"
}
check_lightpath_list() {
  local out; out="$("${HERE}/lightpath.sh" list)" || return 1
  printf '%s\n' "${out}"
  printf '%s' "${out}" | grep -q "id=" || { echo "no flow listed"; return 1; }
}
check_correlate() {
  local out; out="$("${HERE}/correlate.sh")" || return 1
  printf '%s\n' "${out}"
  printf '%s' "${out}" | grep -q "every ONOS flow maps to a live lightpath" \
    || { echo "a flow has no twin service"; return 1; }
  "${HERE}/correlate.sh" --json | python3 -c 'import json,sys; rows=json.load(sys.stdin); assert rows, "empty join"; print(len(rows), "rows")'
}
opm_status() {
  # twin_curl: the twin may require TWINLIGHT_API_TOKEN (see --help, tokens).
  twin_curl -sS "${TWIN_URL}/internal/opm/$1" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","ok"))'
}
# With TWINLIGHT_API_TOKENS set for the twin, prove the stack really runs with
# authentication on: the twin refuses a request without a token, and accepts
# the one the adapter and the scripts present.
check_tokens() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "${TWIN_URL}/restconf/data/tapi-common:context")"
  [ "${code}" = 401 ] || { echo "the twin answered ${code} without a token, not 401"; return 1; }
  code="$(twin_curl -s -o /dev/null -w '%{http_code}' "${TWIN_URL}/restconf/data/tapi-common:context")"
  [ "${code}" = 200 ] || { echo "the twin answered ${code} to TWINLIGHT_API_TOKEN, not 200"; return 1; }
  curl -sS "${ADAPTER_URL}/health" | python3 -c '
import json, sys
h = json.load(sys.stdin)
assert h["token-configured"] and h["twin-authorized"], h
print("the twin refuses an anonymous request and accepts the adapter and the scripts")
'
}
check_fault_cut() {
  local out
  out="$("${HERE}/fault.sh" cut-path "${SMOKE_UUID}")" || { printf '%s\n' "${out}"; return 1; }
  printf '%s\n' "${out}"
  [ "$(opm_status "${SMOKE_UUID}")" = "link-failed" ] || { echo "OPM did not report link-failed"; return 1; }
  "${HERE}/fault.sh" list | tee /dev/stderr | grep -q "(none)" && { echo "fault list is empty after a cut"; return 1; }
  return 0
}
check_fault_heal() {
  "${HERE}/fault.sh" heal-all || return 1
  "${HERE}/fault.sh" list | tee /dev/stderr | grep -q "(none)" || { echo "fibers still cut"; return 1; }
  [ "$(opm_status "${SMOKE_UUID}")" = "ok" ] || { echo "OPM did not recover"; return 1; }
}
check_lightpath_delete() {
  local flow
  flow="$(onos_rest_flows | python3 -c '
import sys, json, urllib.request
eps = json.load(urllib.request.urlopen(sys.argv[1] + "/adapter/status")).get("service-endpoints", {})
pair = (eps.get(sys.argv[2]) or {}).get("onos-port-pair")
for line in sys.stdin:
    fid, i, o, state = line.split()
    if "%s->%s" % (i, o) == pair:
        print(fid)
' "${ADAPTER_URL}" "${SMOKE_UUID}")"
  [ -n "${flow}" ] || { echo "no ONOS flow for ${SMOKE_UUID}"; return 1; }
  "${HERE}/lightpath.sh" delete "${flow}" || return 1
  until_true 90 bash -c "! curl -sS '${ADAPTER_URL}/adapter/status' | grep -q '${SMOKE_UUID}'" \
    || { echo "service ${SMOKE_UUID} still on the twin 90 s after the flow was removed"; return 1; }
  echo "flow ${flow} removed; service ${SMOKE_UUID} released"
}
flows_match_services() {
  [ "$(onos_rest_flows | wc -l | tr -d ' ')" = "$(adapter_service_count)" ]
}
# With ONOS_TEST_QOT_MARGIN_DB set, this seeding runs against a twin whose
# admission margin is that value (set before, restored after, whatever
# happens), to check that find_pairs.py follows the twin's margin.
check_seed() {
  local out admitted margin="" rc=0
  echo "SEED_RANDOM_SEED=${SEED_RANDOM_SEED}"
  if [ -n "${ONOS_TEST_QOT_MARGIN_DB:-}" ]; then
    margin="$(twin_margin)" || return 1
    set_twin_margin "${ONOS_TEST_QOT_MARGIN_DB}" || return 1
    echo "qot_margin_db ${margin} -> $(twin_margin) dB for this seeding (ONOS_TEST_QOT_MARGIN_DB)"
  fi
  out="$("${HERE}/seed-demo.sh" --reset)" || rc=1
  if [ -n "${margin}" ]; then
    set_twin_margin "${margin}" || rc=1
    echo "qot_margin_db back to $(twin_margin) dB"
  fi
  printf '%s\n' "${out}"
  [ "${rc}" = 0 ] || return 1
  admitted="$(printf '%s' "${out}" | sed -n 's/.* \([0-9][0-9]*\) lightpath(s) admitted.*/\1/p')"
  [ "${admitted:-0}" -gt 0 ] || { echo "nothing admitted"; return 1; }
  until_true 90 flows_match_services \
    || { echo "ONOS holds $(onos_rest_flows | wc -l | tr -d ' ') REST flows, the adapter $(adapter_service_count) services"; return 1; }
  echo "${admitted} admitted; ONOS flows and twin services agree"
}
# ONOS may delete exactly what the adapter shows it, also after the adapter
# restarts: the adapter keeps ownership on the twin, as the mark it puts in the
# name list of every service it creates, not in its own memory. A lightpath
# created directly on the twin (as the UI would) survives a DELETE sent through
# the adapter, which answers 204 and reports the refusal; an ONOS-created one
# is still released when its flow is removed in ONOS. The adapter is restarted
# only on a stack this run booted.
DELETE_SCOPE_DATA=""; DELETE_SCOPE_NATIVE=""; DELETE_SCOPE_VICTIM=""
adapter_ok() {
  local health
  health="$(curl -sf "${ADAPTER_URL}/health" 2>/dev/null)" || return 1
  printf '%s' "${health}" | python3 -c 'import json,sys; sys.exit(json.load(sys.stdin)["status"] != "ok")'
}
onos_device_available() {
  local device
  device="$(onos_api GET "/onos/v1/devices/${DEVICE_ID}" 2>/dev/null)" || return 1
  printf '%s' "${device}" | python3 -c 'import json,sys; sys.exit(not json.load(sys.stdin).get("available"))' 2>/dev/null
}
twin_service_code() {
  # the twin's status for GET of one connectivity-service: 200 held, 404 gone
  twin_curl -s -o /dev/null -w '%{http_code}' \
    "${DELETE_SCOPE_DATA}/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service=$1"
}
victim_released() { [ "$(twin_service_code "${DELETE_SCOPE_VICTIM}")" = 404 ]; }
listed_to_onos() {
  # the UUIDs the adapter's list GET shows ONOS, sorted, one per line
  curl -sf "${ADAPTER_URL}/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context" 2>/dev/null \
    | python3 -c '
import json, sys
body = json.load(sys.stdin)["tapi-connectivity:connectivity-context"]["connectivity-service"]
print("\n".join(sorted(s["uuid"] for s in body)))' 2>/dev/null
}
DELETE_SCOPE_BEFORE=""
listing_unchanged() { [ "$(listed_to_onos)" = "${DELETE_SCOPE_BEFORE}" ]; }
delete_scope() {
  local body code flow
  body="$(curl -sS "${ADAPTER_URL}/adapter/ports" | python3 -c '
import json, sys
ports = json.load(sys.stdin)["ports"]
def sip(city):
    hits = [p for p in ports if (p["node-name"] or "").lower() == "trx " + city.lower()]
    assert len(hits) == 1, "no single transceiver named %r" % city
    return hits[0]["twin-sip-uuid"]
print(json.dumps({"tapi-connectivity:connectivity-service": [{
    "uuid": sys.argv[1],
    "name": [{"value-name": "service-name", "value": "onos-test: created on the twin"}],
    "end-point": [
        {"local-id": str(i), "service-interface-point": {"service-interface-point-uuid": sip(city)}}
        for i, city in enumerate(sys.argv[2:4], start=1)
    ],
}]}))
' "${DELETE_SCOPE_NATIVE}" "${SMOKE_A}" "${SMOKE_Z}")" || return 1
  code="$(twin_curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/yang-data+json' \
          -d "${body}" "${DELETE_SCOPE_DATA}/tapi-common:context/tapi-connectivity:connectivity-context")"
  [ "${code}" = 201 ] || { echo "the twin answered ${code} to a create sent to it directly"; return 1; }
  echo "created ${DELETE_SCOPE_NATIVE} on the twin directly (${SMOKE_A} -> ${SMOKE_Z})"

  DELETE_SCOPE_BEFORE="$(listed_to_onos)"
  [ -n "${DELETE_SCOPE_BEFORE}" ] || { echo "ONOS is shown no service before the restart"; return 1; }
  if printf '%s\n' "${DELETE_SCOPE_BEFORE}" | grep -qx "${DELETE_SCOPE_NATIVE}"; then
    echo "the twin-native service is listed to ONOS"; return 1
  fi
  echo "ONOS is shown $(printf '%s\n' "${DELETE_SCOPE_BEFORE}" | wc -l | tr -d ' ') service(s), not the twin-native one"

  if [ -n "${STARTED}" ]; then
    docker restart "${TL_PREFIX}-tapi-adapter" >/dev/null || return 1
    until_true 90 adapter_ok || { echo "the adapter is not healthy 90 s after its restart"; return 1; }
    until_true 120 onos_device_available || { echo "the device is not available 120 s after the adapter's restart"; return 1; }
    echo "adapter restarted: its memory is empty"
  else
    echo "--reuse: the adapter is not restarted"
  fi

  until_true 90 listing_unchanged \
    || { printf 'ONOS is shown other services after the restart:\nbefore\n%s\nafter\n%s\n' \
           "${DELETE_SCOPE_BEFORE}" "$(listed_to_onos)"; return 1; }
  [ "$(adapter_service_count)" = "$(printf '%s\n' "${DELETE_SCOPE_BEFORE}" | wc -l | tr -d ' ')" ] \
    || { echo "/adapter/status reports $(adapter_service_count) ONOS-created services after the restart"; return 1; }
  echo "after the restart ONOS is shown the same services, and /adapter/status reports them"

  code="$(curl -s -o /dev/null -w '%{http_code}' -X DELETE \
    "${ADAPTER_URL}/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service=${DELETE_SCOPE_NATIVE}")"
  [ "${code}" = 204 ] || { echo "the adapter answered ${code} to the DELETE, not 204"; return 1; }
  code="$(twin_service_code "${DELETE_SCOPE_NATIVE}")"
  [ "${code}" = 200 ] || { echo "the twin-native service is gone (${code}) after a DELETE through the adapter"; return 1; }
  curl -sS "${ADAPTER_URL}/adapter/status" | python3 -c '
import json, sys
refused = [r["uuid"] for r in json.load(sys.stdin)["refused-deletes"]]
assert sys.argv[1] in refused, "the refusal is not in /adapter/status"
print("DELETE through the adapter: 204, the twin still holds it, refused-deletes reports it")
' "${DELETE_SCOPE_NATIVE}" || return 1

  read -r flow DELETE_SCOPE_VICTIM < <(onos_rest_flows | python3 -c '
import sys, json, urllib.request
eps = json.load(urllib.request.urlopen(sys.argv[1] + "/adapter/status")).get("service-endpoints", {})
by_pair = {ep.get("onos-port-pair"): uuid for uuid, ep in eps.items()}
for line in sys.stdin:
    fid, i, o, state = line.split()
    if state == "ADDED" and "%s->%s" % (i, o) in by_pair:
        print(fid, by_pair["%s->%s" % (i, o)]); break
' "${ADAPTER_URL}")
  [ -n "${DELETE_SCOPE_VICTIM}" ] || { echo "no ADDED ONOS flow joins a service the adapter reports"; return 1; }
  "${HERE}/lightpath.sh" delete "${flow}" || return 1
  until_true 90 victim_released \
    || { echo "service ${DELETE_SCOPE_VICTIM} still on the twin 90 s after its flow was removed"; return 1; }
  echo "flow ${flow} removed in ONOS: the twin released ${DELETE_SCOPE_VICTIM}"
}
check_delete_scope() {
  local rc=0
  DELETE_SCOPE_DATA="$(twin_data)" || return 1
  DELETE_SCOPE_NATIVE="$(python3 -c 'import uuid; print(uuid.uuid4())')"
  delete_scope || rc=$?
  # Whatever happened, the twin-native lightpath goes, so the later steps see
  # a twin holding only what ONOS created.
  twin_curl -s -o /dev/null -X DELETE \
    "${DELETE_SCOPE_DATA}/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service=${DELETE_SCOPE_NATIVE}"
  return "${rc}"
}
# A pair the twin refuses at create must not leave its flow in ONOS. The pairs
# are searched at the twin's own margin and saved; the margin is then raised
# by 3 dB and seed-demo.sh provisions the saved pairs (SEED_PAIRS_FILE), so the
# pairs within 3 dB of their threshold are refused for certain, through the
# same path as any refusal. seed-demo.sh must report each refusal, remove its
# flow and provision spares, and ONOS must end with every flow ADDED. The
# margin is put back whatever happens.
twin_margin() {
  # the margin as find_pairs.py reads it, from the transparent controller
  python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from find_pairs import read_margin; print(read_margin(sys.argv[2]))' \
    "${HERE}" "${TWIN_URL}"
}
set_twin_margin() {
  local code
  code="$(twin_curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
    -d "{\"twin\": {\"rmsa\": {\"qot_margin_db\": $1}}}" "${TWIN_URL}/config/set")"
  [ "${code}" = 200 ] || { echo "the twin answered ${code} to setting qot_margin_db=$1"; return 1; }
}
pending_add_flows() {
  onos_rest_flows | awk '$4 == "PENDING_ADD"' | wc -l | tr -d ' '
}
no_pending_add_flows() { [ "$(pending_add_flows)" = 0 ]; }
check_seed_refusals() {
  local margin raised out rc=0 refused removed pairs="${RUN_DIR}/refusal-step-pairs.txt"
  margin="$(twin_margin)" || return 1
  # Cleared first, so the search sees the line the creates will.
  "${HERE}/lightpath.sh" clear >/dev/null 2>&1 || true
  TWIN_URL="${TWIN_URL}" SEED_RANDOM_SEED="${SEED_RANDOM_SEED}" \
    python3 "${HERE}/find_pairs.py" > "${pairs}" || return 1
  echo "pairs searched at the twin's margin of ${margin} dB (SEED_RANDOM_SEED=${SEED_RANDOM_SEED}):"
  sed 's/^/  /' "${pairs}"
  raised="$(python3 -c 'import sys; print(float(sys.argv[1]) + 3)' "${margin}")"
  set_twin_margin "${raised}" || return 1
  echo "qot_margin_db ${margin} -> ${raised} dB before they are provisioned"
  out="$(SEED_PAIRS_FILE="${pairs}" "${HERE}/seed-demo.sh" --reset)" || rc=1
  set_twin_margin "${margin}" || rc=1
  printf '%s\n' "${out}"
  echo "qot_margin_db back to $(twin_margin) dB"
  [ "${rc}" = 0 ] || return 1
  refused="$(printf '%s' "${out}" | sed -n 's/.* lightpath(s) admitted, \([0-9][0-9]*\) refused.*/\1/p')"
  removed="$(printf '%s\n' "${out}" | grep -c 'removed its flow')"
  [ "${refused:-0}" -gt 0 ] || { echo "nothing was refused: the refusal path did not run"; return 1; }
  [ "${removed}" = "${refused}" ] || { echo "${refused} refused but ${removed} flow(s) removed"; return 1; }
  until_true 60 flows_match_services \
    || { echo "ONOS holds $(onos_rest_flows | wc -l | tr -d ' ') REST flows, the adapter $(adapter_service_count) services"; return 1; }
  # An admitted flow is PENDING_ADD until ONOS's next reconciliation poll,
  # about a minute; one whose create was refused would stay there.
  until_true 150 no_pending_add_flows \
    || { echo "$(pending_add_flows) flow(s) still PENDING_ADD 150 s after seeding"; return 1; }
  echo "${refused} refused, each flow removed; ONOS flows and twin services agree, and every flow reached ADDED"
}
check_resync() {
  local out
  out="$("${HERE}/resync.sh")" || { printf '%s\n' "${out}"; return 1; }
  printf '%s\n' "${out}"
  printf '%s' "${out}" | grep -q "lightpaths survived" || return 1
  onos_api GET "/onos/v1/devices/${DEVICE_ID}/ports" | python3 -c '
import json, sys
ports = json.load(sys.stdin)["ports"]
off = [p["port"] for p in ports if not p.get("isEnabled")]
assert ports and not off, "%d of %d ports disabled after the resync" % (len(off), len(ports))
print(len(ports), "ports, all enabled")
'
}
check_onos_cli() {
  local out
  out="$("${HERE}/onos-cli.sh" devices < /dev/null 2>&1)"; local rc=$?
  printf '%s\n' "${out}"
  [ "${rc}" -eq 0 ] && printf '%s' "${out}" | grep -q "${DEVICE_ID}"
}
check_clear() {
  "${HERE}/lightpath.sh" clear || return 1
  until_true 90 bash -c "[ \"\$(curl -sS '${ADAPTER_URL}/adapter/status' | python3 -c 'import json,sys; print(len(json.load(sys.stdin)[\"onos-created-services\"]))')\" = 0 ]" \
    || { echo "services left on the twin: $(adapter_service_count)"; return 1; }
}

collect_logs() {
  [ -n "${STARTED}${REUSE}" ] || return 0
  local name
  compose ps -a > "${RUN_DIR}/logs/compose-ps.txt" 2>&1
  for name in "${TL_PREFIX}" "${TL_PREFIX}-ui" "${TL_PREFIX}-prometheus" "${TL_PREFIX}-grafana" \
              "${TL_PREFIX}-grafana-init" "${TL_PREFIX}-tapi-adapter" "${TL_PREFIX}-onos"; do
    docker logs "${name}" > "${RUN_DIR}/logs/${name}.log" 2>&1 || true
  done
}

teardown() {
  "${HERE}/demo-down.sh" --purge || return 1
  if [ -n "${RMI}" ]; then
    docker image rm "twinlight:${TWINLIGHT_IMAGE_TAG}" "twinlight-ui:${TWINLIGHT_IMAGE_TAG}" \
      "twinlight-tapi-adapter:${TWINLIGHT_IMAGE_TAG}" || return 1
  fi
  local left
  left="$(docker ps -aq --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}")$(docker volume ls -q \
          --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}")"
  [ -z "${left}" ] || { echo "left behind: ${left}"; return 1; }
  echo "nothing of project ${COMPOSE_PROJECT_NAME} is left"
}

on_interrupt() {
  printf '\ninterrupted\n'
  collect_logs
  if [ -n "${STARTED}" ] && [ -z "${KEEP}" ]; then "${HERE}/demo-down.sh" --purge >/dev/null 2>&1; fi
  summary
  exit 130
}

e2e() {
  trap on_interrupt INT TERM
  printf '%sTest stack%s  project %s, prefix %s, tag %s\n' "${C_BOLD}" "${C_RESET}" \
    "${COMPOSE_PROJECT_NAME}" "${TL_PREFIX}" "${TWINLIGHT_IMAGE_TAG}"
  printf '            twin %s  adapter %s  ONOS %s  device %s\n' \
    "${TWIN_URL}" "${ADAPTER_URL}" "${ONOS_URL}" "${DEVICE_ID}"
  printf '            seeding with SEED_RANDOM_SEED=%s\n' "${SEED_RANDOM_SEED}"
  printf '            authentication %s\n\n' \
    "$([ -n "${TWINLIGHT_API_TOKENS:-}" ] && echo "on (TWINLIGHT_API_TOKENS for the twin, TWINLIGHT_API_TOKEN for the adapter and scripts)" || echo off)"

  if [ -n "${REUSE}" ]; then
    step "preflight (reuse a running stack)" reuse_preflight || return
  else
    step "preflight (nothing else in the way)" preflight || return
    local boot
    STARTED=yes
    for boot in $(seq 1 "${BOOTS}"); do
      step "cold boot ${boot}/${BOOTS}: down -v, demo-up.sh" cold_boot || { collect_logs; return; }
    done
  fi

  step "validate.sh" "${HERE}/validate.sh"
  if [ -n "${TWINLIGHT_API_TOKENS:-}" ]; then
    step "authentication on: anonymous refused, the token accepted" check_tokens
  fi
  step "lightpath.sh create ${SMOKE_A} ${SMOKE_Z}" check_lightpath_create
  SMOKE_UUID="$(smoke_uuid)"
  step "lightpath.sh list" check_lightpath_list
  step "correlate.sh (table and --json)" check_correlate
  if [ -n "${SMOKE_UUID}" ]; then
    step "fault.sh cut-path, OPM reports link-failed" check_fault_cut
    step "fault.sh heal-all, OPM recovers" check_fault_heal
    step "lightpath.sh delete, the twin releases the service" check_lightpath_delete
  fi
  step "seed-demo.sh --reset, ONOS flows match twin services" check_seed
  step "correlate.sh after seeding" check_correlate
  step "seed-demo.sh with refusals: their flows are removed" check_seed_refusals
  step "delete scope, across an adapter restart" check_delete_scope
  step "resync.sh --check" "${HERE}/resync.sh" --check
  step "resync.sh, lightpaths survive, ports enabled" check_resync
  step "onos-cli.sh devices" check_onos_cli
  step "lightpath.sh clear, the twin is emptied" check_clear
  step "validate.sh after the smoke pass" "${HERE}/validate.sh"

  collect_logs
  if [ -n "${REUSE}" ]; then
    echo "--reuse: the stack is left as it was found running"
  elif [ -n "${KEEP}" ]; then
    printf '\n--keep: the stack is still up. Tear it down with the same environment:\n'
    printf '  COMPOSE_PROJECT_NAME=%s TWINLIGHT_CONTAINER_PREFIX=%s ... %s/demo-down.sh --purge\n' \
      "${COMPOSE_PROJECT_NAME}" "${TL_PREFIX}" "${HERE}"
  else
    step "demo-down.sh --purge, nothing left" teardown
  fi
}

printf '%sTwinLight <-> ONOS integration tests%s   run directory: %s\n\n' "${C_BOLD}" "${C_RESET}" "${RUN_DIR}"
case "${MODE}" in
  unit) step "adapter unit tests${PINNED:+ (pinned, in a container)}" unit_tests ;;
  e2e)  e2e ;;
  all)  step "adapter unit tests${PINNED:+ (pinned, in a container)}" unit_tests; e2e ;;
  preflight) step "preflight (nothing else in the way)" preflight ;;
esac
summary
[ "$(failures)" -eq 0 ]
