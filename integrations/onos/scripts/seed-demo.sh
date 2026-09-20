#!/usr/bin/env bash
# Populate the demo with a spread of lightpaths, all provisioned through ONOS,
# so the ONOS GUI, the TwinLight UI and Grafana all have something to show
# before you start presenting.
#
#   seed-demo.sh            provision the default set
#   seed-demo.sh --reset    clear existing ONOS flows first
#
# Every lightpath here is created the same way Act 2 does it: an ONOS flow rule
# on the OLS device, which the ols driver turns into a T-API connectivity
# service. Nothing talks to the twin's connectivity API directly.
#
# The pairs are chosen to span the QoT range rather than to look uniformly
# healthy — short regional hops that close comfortably, and longer hauls that
# the twin admits with visibly degraded GSNR. A demo where every number is
# green is a demo that does not show the twin doing anything.

. "$(dirname "$0")/lib.sh"

require_stack

# A-city  Z-city  what it illustrates
PAIRS="
Albany:Baltimore:regional hop, comfortable margin
Boston:New_York:short haul, best-case GSNR
Los_Angeles:San_Diego:west coast regional
Seattle:Portland:pacific northwest regional
Atlanta:Baltimore:medium haul, reduced margin
Chicago:Detroit:midwest regional
Dallas:Houston:texas regional
Abilene:Atlanta:long haul, heavily degraded
"

if [ "${1:-}" = "--reset" ]; then
  say "Clearing existing ONOS flows"
  "$(dirname "$0")/lightpath.sh" clear >/dev/null 2>&1 || true
fi

say "Seeding lightpaths through ONOS"
printf '\n'

created=0
refused=0

# Iterate line by line: the labels contain spaces, so an unquoted `for` over
# $PAIRS would split each description into separate loop iterations.
while IFS=':' read -r a_city z_city label; do
  [ -n "${a_city}" ] || continue

  a_port="$(port_for_node "${a_city}" 2>/dev/null)" || { warn "skipping ${a_city}: not found"; continue; }
  z_port="$(port_for_node "${z_city}" 2>/dev/null)" || { warn "skipping ${z_city}: not found"; continue; }

  before="$(curl -sS "${ADAPTER_URL}/adapter/status" \
    | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["onos-created-services"]))')"

  body="$(printf '{"priority":100,"timeout":0,"isPermanent":true,"deviceId":"%s",' "${DEVICE_ID}"
          printf '"treatment":{"instructions":[{"type":"OUTPUT","port":"%s"}]},' "${z_port}"
          printf '"selector":{"criteria":[{"type":"IN_PORT","port":"%s"}]}}' "${a_port}")"

  printf '  %-14s -> %-14s  port %2s -> %-2s  %s\n' \
    "${a_city}" "${z_city}" "${a_port}" "${z_port}" "(${label})"

  onos_api POST "/onos/v1/flows/${DEVICE_ID}?appId=org.onosproject.rest" "${body}" >/dev/null \
    || { warn "    ONOS rejected the flow rule"; continue; }

  # The driver POSTs asynchronously; GNPy propagation for a long path takes a
  # few seconds, so poll rather than sleeping a fixed amount.
  admitted=""
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    sleep 3
    now="$(curl -sS "${ADAPTER_URL}/adapter/status" \
      | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["onos-created-services"]))')"
    if [ "${now}" -gt "${before}" ]; then admitted=yes; break; fi
  done

  if [ -n "${admitted}" ]; then
    created=$((created + 1))
  else
    refused=$((refused + 1))
    printf '    refused by the twin (see %s/adapter/status)\n' "${ADAPTER_URL}"
  fi
done <<EOF
${PAIRS}
EOF

printf '\n'
ok "${created} lightpath(s) admitted, ${refused} refused"

say "Twin optical performance monitoring"
curl -sS "${ADAPTER_URL}/adapter/status" | python3 -c "
import json, sys, urllib.request
svcs = json.load(sys.stdin)['onos-created-services']
rows = []
for u in svcs:
    try:
        opm = json.load(urllib.request.urlopen('${TWIN_URL}/internal/opm/' + u))
        info = json.load(urllib.request.urlopen('${TWIN_URL}/internal/services/' + u))
    except Exception:
        continue
    m = opm['measurements']
    hops = [h['uid'].replace('roadm ', '').replace('trx ', '')
            for h in (info.get('hops') or []) if h.get('type') == 'Transceiver']
    ends = '%s -> %s' % (hops[0], hops[-1]) if len(hops) >= 2 else u[:8]
    rows.append((m.get('gsnr-db') if isinstance(m.get('gsnr-db'), (int, float)) else -99,
                 ends, info.get('total-fiber-km', 0), m))
rows.sort(reverse=True)
for gsnr, ends, km, m in rows:
    fmt = lambda v: ('%6.2f' % v) if isinstance(v, (int, float)) else '%6s' % '--'
    print('    %-34s %7.1f km  GSNR %s dB  OSNR %s dB  BER %.2e'
          % (ends, km, fmt(m['gsnr-db']), fmt(m['osnr-db']), m['pre-fec-ber']))
"

cat <<EOF

${C_BOLD}Where to look now${C_RESET}

  ONOS GUI      http://localhost:8181/onos/ui   (onos / rocks)
                Devices  -> click the OLS row -> the ports icon in the panel
                Flows    -> one ADDED flow per admitted lightpath
  TwinLight UI  http://localhost:5173           lightpaths on the map, spectrum heat map
  Grafana       http://localhost:3000           OPM series, one per lightpath

EOF
