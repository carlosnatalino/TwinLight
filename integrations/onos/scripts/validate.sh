#!/usr/bin/env bash
# End-to-end validation of the TwinLight <-> ONOS integration.
#
#   integrations/onos/scripts/validate.sh
#
# Runs 16 checks across all three layers and exits non-zero if any fail. Run it
# once before the talk; if it is green, the demo will work.
#
# The A/Z pair defaults to a CORONET CONUS pair chosen so that the twin's
# baseline GSNR sits between the DP-QPSK and DP-16QAM thresholds — that is what
# makes check 13 (admit) and check 14 (refuse) both meaningful. Override for
# another topology:
#
#   VALIDATE_A=Albany VALIDATE_Z=Baltimore integrations/onos/scripts/validate.sh

. "$(dirname "$0")/lib.sh"

A_CITY="${VALIDATE_A:-Abilene}"
Z_CITY="${VALIDATE_Z:-Atlanta}"

PASS=0; FAIL=0
check() {
  # check <description> <command...>
  local desc="$1"; shift
  if "$@" >/tmp/twinlight-validate.$$ 2>&1; then
    printf '%s  PASS%s  %s\n' "${C_GREEN}" "${C_RESET}" "${desc}"
    PASS=$((PASS + 1))
  else
    printf '%s  FAIL%s  %s\n' "${C_RED}${C_BOLD}" "${C_RESET}" "${desc}"
    sed 's/^/          /' /tmp/twinlight-validate.$$ | head -6
    FAIL=$((FAIL + 1))
  fi
  rm -f /tmp/twinlight-validate.$$
}
note() { printf '        %s\n' "$*"; }

printf '\n%sTwinLight <-> ONOS integration validation%s\n\n' "${C_BOLD}" "${C_RESET}"

# --- Layer 1: the twin -----------------------------------------------------
say "Layer 1 — TwinLight digital twin"

check "twin /health returns ok" \
  bash -c "curl -sSf '${TWIN_URL}/health' | grep -q '\"status\":\"ok\"'"

check "twin serves T-API v2.6.0 SIPs" \
  bash -c "curl -sSf '${TWIN_URL}/data/tapi-common:context/service-interface-point' \
    | python3 -c 'import json,sys; n=len(json.load(sys.stdin)[\"tapi-common:context\"][\"service-interface-point\"]); assert n>0, \"no SIPs\"; print(n)'"

# --- Layer 2: the adapter --------------------------------------------------
say "Layer 2 — T-API 2.6 to 2.1 adapter"

check "adapter /health reports the twin reachable" \
  bash -c "curl -sSf '${ADAPTER_URL}/health' | grep -q '\"twin-reachable\":true'"

check "adapter serves /restconf/data/tapi-common:context" \
  bash -c "curl -sSf '${ADAPTER_URL}/restconf/data/tapi-common:context' >/dev/null"

# The three things ONOS's TapiDeviceDescriptionDiscovery dereferences without
# a null check. Any one missing means zero ports discovered.
check "every SIP satisfies ONOS checkValidEndpoint() and parseTapiPorts()" \
  bash -c "curl -sSf '${ADAPTER_URL}/restconf/data/tapi-common:context' | python3 -c '
import json, sys
sips = json.load(sys.stdin)[\"tapi-common:context\"][\"service-interface-point\"]
assert sips, \"no SIPs\"
for s in sips:
    assert \"PHOTONIC_MEDIA\" in s[\"layer-protocol-name\"], s[\"uuid\"]
    assert \"PHOTONIC_LAYER_QUALIFIER_NMC\" in s[\"supported-layer-protocol-qualifier\"], s[\"uuid\"]
    mc = s[\"tapi-photonic-media:media-channel-service-interface-point-spec\"][\"mc-pool\"]
    spec = mc.get(\"available-spectrum\") or mc[\"supportable-spectrum\"]
    fc = spec[0][\"frequency-constraint\"]
    assert fc[\"grid-type\"] == \"DWDM\", fc
    # ONOS getChannelSpacing() only matches these two labels without a typo.
    assert fc[\"adjustment-granularity\"] in (\"G_50GHZ\", \"G_25GHZ\"), fc
print(len(sips))
'"

check "SIP UUIDs survive ONOS PortNumber.portNumber() (UnsignedLongs.decode)" \
  bash -c "curl -sSf '${ADAPTER_URL}/restconf/data/tapi-common:context' | python3 -c '
import json, sys
sips = json.load(sys.stdin)[\"tapi-common:context\"][\"service-interface-point\"]
seen = set()
for s in sips:
    tail = s[\"uuid\"].split(\"-\")[-1]
    assert tail.isdigit(), \"non-numeric tail %r would throw NumberFormatException\" % tail
    assert not tail.startswith(\"0\"), \"leading zero %r is parsed as octal\" % tail
    assert tail not in seen, \"duplicate port number %s\" % tail
    seen.add(tail)
print(len(seen))
'"

check "per-SIP resource is unwrapped for TapiDeviceLambdaQuery" \
  bash -c "
    uuid=\$(curl -sSf '${ADAPTER_URL}/restconf/data/tapi-common:context' \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"tapi-common:context\"][\"service-interface-point\"][0][\"uuid\"])')
    curl -sSf \"${ADAPTER_URL}/restconf/data/tapi-common:context/service-interface-point=\$uuid\" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert \"tapi-photonic-media:media-channel-service-interface-point-spec\" in d, \\
    \"mc-pool must be at the top level, not wrapped in tapi-common:context\"
'"

# --- Layer 3: ONOS ---------------------------------------------------------
say "Layer 3 — ONOS"

check "ONOS core services registered (not just answering on :8181)" onos_ready

check "ODTN apps active (odtn-service, drivers.odtn-driver, optical-model, restsb)" \
  bash -c "curl -sSf -u '${ONOS_AUTH}' '${ONOS_URL}/onos/v1/applications' | python3 -c '
import json, sys
active = {a[\"name\"] for a in json.load(sys.stdin)[\"applications\"] if a[\"state\"] == \"ACTIVE\"}
missing = [n for n in (\"org.onosproject.odtn-service\",
                       \"org.onosproject.drivers.odtn-driver\",
                       \"org.onosproject.optical-model\",
                       \"org.onosproject.restsb\") if n not in active]
assert not missing, \"inactive: %s\" % missing
'"

check "twin registered as an available OLS device" \
  bash -c "curl -sSf -u '${ONOS_AUTH}' '${ONOS_URL}/onos/v1/devices/${DEVICE_ID}' | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d[\"available\"], \"device present but not available\"
assert d[\"type\"] == \"OLS\", \"type is %s, expected OLS\" % d[\"type\"]
'"

check "ONOS port count matches the twin's SIP count" \
  bash -c "
    sips=\$(curl -sSf '${ADAPTER_URL}/health' | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"sips\"])')
    ports=\$(curl -sSf -u '${ONOS_AUTH}' '${ONOS_URL}/onos/v1/devices/${DEVICE_ID}/ports' \
      | python3 -c 'import json,sys; print(len(json.load(sys.stdin)[\"ports\"]))')
    [ \"\$sips\" -gt 0 ] && [ \"\$sips\" = \"\$ports\" ] || { echo \"SIPs=\$sips ports=\$ports\"; exit 1; }
    echo \"\$ports ports\"
  "

check "discovered ports are OCh ports carrying the twin's SIP UUID" \
  bash -c "curl -sSf -u '${ONOS_AUTH}' '${ONOS_URL}/onos/v1/devices/${DEVICE_ID}/ports' | python3 -c '
import json, sys
ports = json.load(sys.stdin)[\"ports\"]
assert ports, \"no ports\"
och = [p for p in ports if p.get(\"type\") == \"och\"]
assert och, \"no port has type och; found %s\" % {p.get(\"type\") for p in ports}
assert any(\"uuid\" in (p.get(\"annotations\") or {}) for p in och), \"SIP uuid annotation missing\"
'"

# --- Layer 4: end-to-end provisioning --------------------------------------
say "Layer 4 — end-to-end provisioning (ONOS drives, the twin decides)"

A_PORT="$(port_for_node "${A_CITY}")" || die "unknown transceiver ${A_CITY}"
Z_PORT="$(port_for_node "${Z_CITY}")" || die "unknown transceiver ${Z_CITY}"
note "using ${A_CITY} (port ${A_PORT}) -> ${Z_CITY} (port ${Z_PORT})"

flow_body() {
  printf '{"priority":100,"timeout":0,"isPermanent":true,"deviceId":"%s",' "${DEVICE_ID}"
  printf '"treatment":{"instructions":[{"type":"OUTPUT","port":"%s"}]},' "$2"
  printf '"selector":{"criteria":[{"type":"IN_PORT","port":"%s"}]}}' "$1"
}

set_modulation() {
  curl -sSf -X POST -H 'Content-Type: application/json' \
    -d "{\"modulation-format\":\"$1\"}" "${ADAPTER_URL}/adapter/modulation" >/dev/null
}

svc_count() {
  curl -sS "${ADAPTER_URL}/adapter/status" \
    | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["onos-created-services"]))'
}
# Identity of the most recent rejection, or "none". NOT a count: /adapter/status
# returns only rejections[-10:], so a count saturates at 10 and then never
# changes again -- which made the DP-16QAM check below fail spuriously on any
# stack that had already seen ten refusals.
rej_latest() {
  curl -sS "${ADAPTER_URL}/adapter/status" | python3 -c '
import json, sys
r = json.load(sys.stdin)["recent-rejections"]
print("%s@%s" % (r[-1]["uuid"], r[-1]["at"]) if r else "none")
'
}

check "DP-QPSK: ONOS flow rule becomes a lightpath on the twin" \
  bash -c "
    $(declare -f set_modulation svc_count flow_body onos_api)
    ONOS_URL='${ONOS_URL}'; ONOS_AUTH='${ONOS_AUTH}'; ADAPTER_URL='${ADAPTER_URL}'; DEVICE_ID='${DEVICE_ID}'
    set_modulation DP-QPSK
    before=\$(svc_count)
    onos_api POST '/onos/v1/flows/${DEVICE_ID}?appId=org.onosproject.rest' \"\$(flow_body ${A_PORT} ${Z_PORT})\" >/dev/null
    for i in \$(seq 1 24); do
      sleep 5
      [ \"\$(svc_count)\" -gt \"\$before\" ] && exit 0
    done
    echo \"no new service after 120s (before=\$before now=\$(svc_count))\"; exit 1
  "

check "the lightpath carries the T-API spectrum augment and live OPM" \
  bash -c "curl -sSf '${ADAPTER_URL}/adapter/status' | python3 -c '
import json, sys, urllib.request
sys.path.insert(0, \"${SCRIPT_DIR}\")
from tapi_fields import spectrum_of
svcs = json.load(sys.stdin)[\"onos-created-services\"]
assert svcs, \"no ONOS-created services\"
uuid, payload = next(iter(svcs.items()))
svc = payload[\"tapi-connectivity:connectivity-service\"]
# T-API 2.6 has no frequency-slot leaf; spectrum is an end-point augment.
assert \"frequency-slot\" not in svc, \"non-standard frequency-slot key is back\"
band = spectrum_of(svc)
assert band, \"no spectrum allocated\"
opm = json.load(urllib.request.urlopen(\"${TWIN_URL}/internal/opm/\" + uuid))[\"measurements\"]
assert \"gsnr-db\" in opm and \"pre-fec-ber\" in opm, opm
print(\"%s at %.4f THz / %.2f GHz, GSNR %.2f dB\" % (uuid[:8], band[0], band[1], opm[\"gsnr-db\"]))
'"

check "DP-16QAM: the twin REFUSES the same path on QoT grounds" \
  bash -c "
    $(declare -f set_modulation rej_latest flow_body onos_api)
    ONOS_URL='${ONOS_URL}'; ONOS_AUTH='${ONOS_AUTH}'; ADAPTER_URL='${ADAPTER_URL}'; DEVICE_ID='${DEVICE_ID}'
    set_modulation DP-16QAM
    before=\$(rej_latest)
    onos_api POST '/onos/v1/flows/${DEVICE_ID}?appId=org.onosproject.rest' \"\$(flow_body ${Z_PORT} ${A_PORT})\" >/dev/null
    for i in \$(seq 1 24); do
      sleep 5
      if [ \"\$(rej_latest)\" != \"\$before\" ]; then
        curl -sS '${ADAPTER_URL}/adapter/status' | python3 -c '
import json, sys
r = json.load(sys.stdin)[\"recent-rejections\"][-1]
assert r[\"reason\"] == \"rmsa-qot-refused\", r
print(r[\"detail\"])
'
        exit 0
      fi
    done
    echo \"twin admitted a DP-16QAM path that should have failed QoT — pick a longer VALIDATE_A/VALIDATE_Z pair\"
    exit 1
  "
set_modulation DP-QPSK >/dev/null 2>&1 || true

check "removing the ONOS flow releases the lightpath on the twin" \
  bash -c "
    $(declare -f svc_count onos_api)
    ONOS_URL='${ONOS_URL}'; ONOS_AUTH='${ONOS_AUTH}'; ADAPTER_URL='${ADAPTER_URL}'; DEVICE_ID='${DEVICE_ID}'
    ids=\$(onos_api GET '/onos/v1/flows/${DEVICE_ID}' | python3 -c '
import json, sys
for f in json.load(sys.stdin).get(\"flows\", []):
    if f.get(\"appId\") == \"org.onosproject.rest\":
        print(f[\"id\"])
')
    [ -n \"\$ids\" ] || { echo 'no rest-app flows to remove'; exit 1; }
    for id in \$ids; do onos_api DELETE \"/onos/v1/flows/${DEVICE_ID}/\$id\" >/dev/null; done
    for i in \$(seq 1 24); do
      sleep 5
      [ \"\$(svc_count)\" -eq 0 ] && exit 0
    done
    echo \"services still present after 120s: \$(svc_count)\"; exit 1
  "

printf '\n%s%d passed, %d failed%s\n\n' \
  "$([ "${FAIL}" -eq 0 ] && printf '%s' "${C_GREEN}${C_BOLD}" || printf '%s' "${C_RED}${C_BOLD}")" \
  "${PASS}" "${FAIL}" "${C_RESET}"

[ "${FAIL}" -eq 0 ] || exit 1
