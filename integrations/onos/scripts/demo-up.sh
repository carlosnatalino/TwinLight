#!/usr/bin/env bash
# Bring up TwinLight + ONOS and register the twin as an ODTN open line system.
#
#   integrations/onos/scripts/demo-up.sh
#
# Idempotent: safe to re-run. On Apple Silicon expect 2–3 minutes end to end
# once the images are built, almost all of it ONOS's Karaf boot under Rosetta.

. "$(dirname "$0")/lib.sh"

say "Building and starting the stack (twin, ui, prometheus, grafana, adapter, onos)"
compose up -d --build

say "Waiting for the digital twin"
wait_for "twin /health" 300 curl -sSf "${TWIN_URL}/health" \
  || die "twin never became healthy — check: docker logs ${TWIN_CONTAINER}"
ok "twin is up: $(curl -sS "${TWIN_URL}/health")"

say "Waiting for the T-API adapter"
wait_for "adapter /health" 120 curl -sSf "${ADAPTER_URL}/health" \
  || die "adapter never became healthy — check: docker logs ${ADAPTER_CONTAINER}"
ok "adapter is up"

say "Waiting for ONOS (Karaf boot — slow under Rosetta on Apple Silicon)"
# Readiness is judged on the payload, not the HTTP request succeeding: ONOS
# answers on :8181 well before its core services register, returning 503 bodies
# for a minute or more. See onos_ready() in lib.sh.
wait_for "ONOS core services" 900 onos_ready \
  || die "ONOS never came up — check: docker logs ${ONOS_CONTAINER}"
ok "ONOS REST API is up at ${ONOS_URL}/onos/ui (onos / rocks)"

say "Verifying the ODTN apps are active"
# Requests activation of any app ONOS has not been asked to activate (ONOS_APPS
# in the compose file normally has). An ACTIVE state here only means the
# activation was *requested*; whether the apps run is the next step.
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

say "Waiting for ONOS to finish starting the ODTN apps"
# Not the app state above: on a cold boot ONOS reports these apps ACTIVE while a
# Karaf bundle refresh is still to come, and a netcfg pushed before it is lost
# (HTTP 207 "subjectClassKey 'devices' not found", or "Driver not found" and a
# device that never appears). The signal that the refresh is over and the `ols`
# driver registered is each app's /health turning "ready"; see
# onos_device_config_ready() in lib.sh. It must hold twice, 10 s apart.
ready_twice() { onos_device_config_ready && sleep 10 && onos_device_config_ready; }
wait_for "app health 'ready' (${ODTN_APPS})" 900 ready_twice \
  || die "the ODTN apps never reported ready — check: docker logs ${ONOS_CONTAINER}"
ok "ONOS can take the device configuration"

say "Registering ${DEVICE_ID}"
# A configuration ONOS already holds for a device that does not exist is the
# remains of an earlier attempt that lost the race above; re-posting the same
# document is a no-op to ONOS, so remove it first.
if ! onos_device_present && [ "$(onos_device_config_code)" = "200" ]; then
  warn "ONOS holds a configuration for ${DEVICE_ID} but no device — removing it first"
  onos_remove_device_config || die "ONOS kept the stale configuration for ${DEVICE_ID}"
fi
onos_push_netcfg || die "ONOS refused the network configuration"
ok "netcfg accepted"

# With the apps ready the device appears within seconds. If it still does not,
# remove and re-push the configuration, a bounded number of times: ONOS never
# retries a device whose first connection failed.
registered=""
for attempt in 1 2 3; do
  if wait_for "device ${DEVICE_ID} to register" 60 onos_device_present; then
    registered=yes; break
  fi
  [ "${attempt}" -lt 3 ] || break
  warn "device did not register — removing and re-pushing its configuration (${attempt}/2)"
  onos_remove_device_config || warn "the configuration did not clear; re-pushing anyway"
  onos_push_netcfg || die "ONOS refused the network configuration"
done
[ -n "${registered}" ] \
  || die "ONOS never registered ${DEVICE_ID} — check: docker logs ${ONOS_CONTAINER} | grep -i 'driver not found'"

say "Waiting for ONOS to discover the twin and its ports"
wait_for "device ${DEVICE_ID} available" 180 bash -c "
  curl -sS -u '${ONOS_AUTH}' '${ONOS_URL}/onos/v1/devices' | python3 -c \"
import json, sys
devs = json.load(sys.stdin)['devices']
sys.exit(0 if any(d['id'] == '${DEVICE_ID}' and d['available'] for d in devs) else 1)
\"" || die "ONOS never marked ${DEVICE_ID} available — check: docker logs ${ONOS_CONTAINER}"

wait_for "port discovery" 180 bash -c "
  curl -sS -u '${ONOS_AUTH}' '${ONOS_URL}/onos/v1/devices/${DEVICE_ID}/ports' | python3 -c \"
import json, sys
sys.exit(0 if len(json.load(sys.stdin).get('ports', [])) > 0 else 1)
\"" || die "ONOS discovered the device but zero ports — see integrations/onos/README.md § Troubleshooting"

# Read now, not when the adapter came up: its SIP catalogue fills on ONOS's
# first request, so until discovery it reports 0.
sip_count="$(curl -sS "${ADAPTER_URL}/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["sips"])')"
discovered="$(onos_api GET "/onos/v1/devices/${DEVICE_ID}/ports" \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["ports"]))')"
ok "ONOS discovered ${discovered} optical ports (twin has ${sip_count} SIPs)"

cat <<EOF

${C_BOLD}Stack is up.${C_RESET}

  ONOS GUI        ${ONOS_URL}/onos/ui          (onos / rocks)
  ONOS CLI        integrations/onos/scripts/onos-cli.sh
  TwinLight UI    ${UI_URL}
  Grafana         ${GRAFANA_URL}        (admin / admin)
  Adapter status  ${ADAPTER_URL}/adapter/status

Next:
  integrations/onos/scripts/validate.sh                     # prove the integration works
  integrations/onos/scripts/lightpath.sh create Abilene Atlanta
EOF
