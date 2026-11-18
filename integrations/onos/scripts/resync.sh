#!/usr/bin/env bash
# Force ONOS to re-discover the twin's ports.
#
#   resync.sh          bounce the device in netcfg and wait for re-discovery
#   resync.sh --check  report what ONOS currently holds vs. what the twin has
#
# WHY THIS EXISTS
#
# ONOS does not poll the twin for anything after the initial discovery. Two
# separate upstream behaviours cause this, and neither is configurable:
#
#   1. RestDeviceProvider.checkAndUpdateDevice() re-runs port discovery only
#      when the port list is already empty:
#
#          //if ports are not discovered, retry the discovery
#          if (deviceService.getPorts(deviceId).isEmpty()) {
#              discoverPorts(deviceId);
#          }
#
#      So once 75 ports exist, discoverPortDetails() is never called again and
#      no change to the SIP list or its spectrum reaches ONOS.
#
#   2. TapiFlowRuleProgrammable.getFlowEntries() reads ONOS's own
#      DeviceConnectionCache, never the device. The call that would read back
#      the device's connectivity services is commented out upstream:
#
#          //TODO this is a blocking call on ADVA OLS, right now using cache.
#          //return getFlowsFromConnectivityServices(deviceId);
#
#      So a lightpath created anywhere other than through ONOS can never appear
#      in the Flows view, and this script cannot change that.
#
# What a resync DOES refresh: the port list and each port's lambda/SIP
# annotations. What it does NOT: the Flows view.

. "$(dirname "$0")/lib.sh"

# Where the twin serves T-API, read once from its host-meta.
TWIN_DATA="$(twin_data)"

require_stack

onos_port_count() {
  onos_api GET "/onos/v1/devices/${DEVICE_ID}/ports" 2>/dev/null \
    | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("ports", [])))' 2>/dev/null \
    || echo 0
}

twin_sip_count() {
  curl -sS "${ADAPTER_URL}/health" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["sips"])'
}

onos_flow_count() {
  onos_api GET "/onos/v1/flows/${DEVICE_ID}" 2>/dev/null | python3 -c "
import json, sys
print(len([f for f in json.load(sys.stdin).get('flows', [])
           if f.get('appId') == 'org.onosproject.rest']))
" 2>/dev/null || echo 0
}

twin_service_count() {
  curl -sS "${TWIN_DATA}/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service" \
    | python3 -c "
import json, sys
print(len(json.load(sys.stdin)['tapi-connectivity:connectivity-context']['connectivity-service']))
"
}

report() {
  printf '     ONOS ports        %s\n' "$(onos_port_count)"
  printf '     twin SIPs         %s\n' "$(twin_sip_count)"
  printf '     ONOS flows        %s\n' "$(onos_flow_count)"
  printf '     twin services     %s   (ONOS-created: %s)\n' \
    "$(twin_service_count)" \
    "$(curl -sS "${ADAPTER_URL}/adapter/status" \
        | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["onos-created-services"]))')"
}

if [ "${1:-}" = "--check" ]; then
  say "Current state"
  report
  cat <<'EOF'

  A gap between "twin services" and "ONOS flows" is expected and is not a bug:
  lightpaths created in the TwinLight UI are invisible to ONOS by design, and
  resyncing will not surface them. Only the port list can be refreshed.
EOF
  exit 0
fi

say "State before resync"
report

# ONOS re-runs discoverPortDetails() only for a device with no ports
# (RestDeviceProvider.checkAndUpdateDevice, quoted above). Removing the device's
# netcfg disconnects it but keeps its ports in the store, disabled, so a
# remove-and-re-add on its own rediscovers nothing and leaves every port
# disabled. The ports therefore have to go too, and ONOS removes a device's
# ports only while the device is unavailable
# (DeviceManager.removeDevicePorts, CLI `device-ports-remove`; no REST
# resource), so: remove the netcfg, wait for the device to go unavailable,
# remove its ports, re-add the netcfg. The device itself and its flow rules stay.
say "Removing the device from netcfg"
curl -sS -u "${ONOS_AUTH}" -X DELETE \
  "${ONOS_URL}/onos/v1/network/configuration/devices/${DEVICE_ID}" >/dev/null \
  || die "could not remove the device configuration"

wait_for "device to go unavailable" 60 bash -c "
  ! curl -sS -u '${ONOS_AUTH}' '${ONOS_URL}/onos/v1/devices/${DEVICE_ID}' 2>/dev/null \
    | python3 -c \"
import json, sys
d = json.load(sys.stdin)
sys.exit(0 if d.get('available') else 1)
\" 2>/dev/null" || warn "device did not drop cleanly; continuing"

say "Removing its ports from ONOS's store"
ports_left() {
  onos_api GET "/onos/v1/devices/${DEVICE_ID}/ports" 2>/dev/null \
    | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("ports", [])))' 2>/dev/null \
    || echo "?"
}
ports_cleared() { [ "$(ports_left)" = 0 ]; }
if "${SCRIPT_DIR}/onos-cli.sh" "device-ports-remove ${DEVICE_ID}" >/dev/null 2>&1 \
   && wait_for "ports to clear" 30 ports_cleared; then
  ok "ports removed"
else
  warn "could not remove the ports ($(ports_left) left); ONOS will keep the old, disabled port list"
fi

say "Re-adding the device"
onos_push_netcfg || die "ONOS refused the network configuration"

rediscovered() {
  onos_device_present --available || return 1
  onos_api GET "/onos/v1/devices/${DEVICE_ID}/ports" 2>/dev/null | python3 -c "
import json, sys
ports = json.load(sys.stdin).get('ports', [])
sys.exit(0 if ports and all(p.get('isEnabled') for p in ports) else 1)
"
}
wait_for "device available, ports re-discovered and enabled" 180 rediscovered \
  || die "ports did not come back enabled — check: docker logs ${ONOS_CONTAINER}"

say "State after resync"
report

# The hazard worth checking every time: on reconnect the driver calls
# removeInitalConnectivityServices(), which deletes every connectivity service
# it can see IF ONOS's flow cache for the device is empty. Twin-native services
# are hidden from ONOS so they are safe, but ONOS-created ones are visible.
after="$(curl -sS "${ADAPTER_URL}/adapter/status" \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["onos-created-services"]))')"
printf '\n'
if [ "${after}" = "0" ] && [ "$(onos_flow_count)" != "0" ]; then
  warn "ONOS-created lightpaths were torn down by removeInitalConnectivityServices()"
  warn "re-provision them with: integrations/onos/scripts/seed-demo.sh --reset"
else
  ok "resync complete; ONOS-created lightpaths survived"
fi
