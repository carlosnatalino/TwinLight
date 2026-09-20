#!/usr/bin/env bash
# Bring up TwinLight + ONOS and register the twin as an ODTN open line system.
#
#   integrations/onos/scripts/demo-up.sh
#
# Idempotent: safe to re-run. On Apple Silicon expect 3–6 minutes end to end,
# almost all of it ONOS's Karaf boot under Rosetta.

. "$(dirname "$0")/lib.sh"

say "Building and starting the stack (twin, ui, prometheus, grafana, adapter, onos)"
compose up -d --build

say "Waiting for the digital twin"
wait_for "twin /health" 300 curl -sSf "${TWIN_URL}/health" \
  || die "twin never became healthy — check: docker logs twinlight"
ok "twin is up: $(curl -sS "${TWIN_URL}/health")"

say "Waiting for the T-API adapter"
wait_for "adapter /health" 120 curl -sSf "${ADAPTER_URL}/health" \
  || die "adapter never became healthy — check: docker logs twinlight-tapi-adapter"
sip_count="$(curl -sS "${ADAPTER_URL}/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["sips"])')"
ok "adapter is up"

say "Waiting for ONOS (Karaf boot — slow under Rosetta on Apple Silicon)"
# Readiness is judged on the payload, not the HTTP request succeeding: ONOS
# answers on :8181 well before its core services register, returning 503 bodies
# for a minute or more. See onos_ready() in lib.sh.
wait_for "ONOS core services" 900 onos_ready \
  || die "ONOS never came up — check: docker logs twinlight-onos"
ok "ONOS REST API is up at ${ONOS_URL}/onos/ui (onos / rocks)"

say "Verifying the ODTN apps are active"
# drivers.odtn-driver is the bundle that registers the "ols" driver and binds
# the TapiDevice* behaviours to it. ONOS 2.7 has no /onos/v1/drivers REST
# resource, so the bundle's app state is the checkable proxy; that the driver
# really works is proven below, by ports actually being discovered.
for app in odtn-service drivers.odtn-driver optical-model restsb; do
  if onos_app_active "${app}"; then
    ok "org.onosproject.${app} ACTIVE"
  else
    warn "org.onosproject.${app} not active — activating"
    onos_api POST "/onos/v1/applications/org.onosproject.${app}/active" >/dev/null || true
    wait_for "org.onosproject.${app}" 120 onos_app_active "${app}" \
      || die "could not activate org.onosproject.${app}"
    ok "org.onosproject.${app} ACTIVE"
  fi
done

say "Pushing the network configuration (registering ${DEVICE_ID})"
netcfg_response="$(curl -sS -u "${ONOS_AUTH}" -X POST \
  -H 'Content-Type: application/json' \
  -d @"${ONOS_DIR}/netcfg/twinlight-ols.json" \
  -w '\n%{http_code}' \
  "${ONOS_URL}/onos/v1/network/configuration")"
netcfg_code="$(printf '%s' "${netcfg_response}" | tail -1)"
[ "${netcfg_code}" = "200" ] || die "netcfg POST returned HTTP ${netcfg_code}: ${netcfg_response}"
ok "netcfg accepted"

say "Waiting for ONOS to discover the twin and its ports"
wait_for "device ${DEVICE_ID} available" 180 bash -c "
  curl -sS -u '${ONOS_AUTH}' '${ONOS_URL}/onos/v1/devices' | python3 -c \"
import json, sys
devs = json.load(sys.stdin)['devices']
sys.exit(0 if any(d['id'] == '${DEVICE_ID}' and d['available'] for d in devs) else 1)
\"" || die "ONOS never marked ${DEVICE_ID} available — check: docker logs twinlight-onos"

wait_for "port discovery" 180 bash -c "
  curl -sS -u '${ONOS_AUTH}' '${ONOS_URL}/onos/v1/devices/${DEVICE_ID}/ports' | python3 -c \"
import json, sys
sys.exit(0 if len(json.load(sys.stdin).get('ports', [])) > 0 else 1)
\"" || die "ONOS discovered the device but zero ports — see integrations/onos/README.md § Troubleshooting"

discovered="$(onos_api GET "/onos/v1/devices/${DEVICE_ID}/ports" \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["ports"]))')"
ok "ONOS discovered ${discovered} optical ports (twin has ${sip_count} SIPs)"

cat <<EOF

${C_BOLD}Stack is up.${C_RESET}

  ONOS GUI        ${ONOS_URL}/onos/ui          (onos / rocks)
  ONOS CLI        integrations/onos/scripts/onos-cli.sh
  TwinLight UI    http://localhost:5173
  Grafana         http://localhost:3000        (admin / admin)
  Adapter status  ${ADAPTER_URL}/adapter/status

Next:
  integrations/onos/scripts/validate.sh                     # prove the integration works
  integrations/onos/scripts/lightpath.sh create Abilene Atlanta
EOF
