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
#
# Each pair also carries a modulation format, so the seeded set spans all
# three the twin supports. Reach and format are not independent: DP-QPSK
# needs 8.5 dB GSNR, DP-16QAM 14.5 and DP-64QAM 20.5, plus rmsa.qot_margin_db
# on top. So the high-order formats are asked for on the short hops that can
# carry them, and the long hauls are asked for at DP-QPSK. That pairing is
# the point — it shows the twin's admission decision depending on the format,
# not just on the route.
#
# ONOS cannot express a modulation (T-API 2.1 has no field for it), so the
# format is adapter policy, set per lightpath through /adapter/modulation.

. "$(dirname "$0")/lib.sh"

require_stack

# Which pairs to provision, and at what format, is *discovered* rather than
# hardcoded -- the same idea as examples/demo_services.py, which retries
# random endpoint pairs until one is admissible. Hardcoding city names means
# re-measuring them by hand whenever the topology or the physics changes, and
# a stale list shows up as a demo full of refusals.
#
# The search probes random pairs through /internal/path-info, which runs the
# real propagation without provisioning anything, and files each probe under
# the *highest* format its GSNR supports. So a long-haul probe fills a
# DP-QPSK slot and a metro probe fills a DP-64QAM one; almost no probe is
# wasted. Only the winners are then provisioned, through ONOS, the same way
# Act 2 does it.
#
# Reach and format are not independent: DP-QPSK needs 8.5 dB, DP-16QAM 14.5
# and DP-64QAM 20.5, each plus rmsa.qot_margin_db. On the bundled CORONET
# topology only metro-distance hops reach DP-64QAM, which is the point worth
# showing -- the twin's answer depends on the format, not just the route.
#
#   SEED_QPSK / SEED_16QAM / SEED_64QAM   how many of each to look for
#   SEED_ATTEMPTS                         probe budget
#   SEED_RANDOM_SEED                      set for a reproducible demo
QPSK_WANTED="${SEED_QPSK:-3}"
Q16_WANTED="${SEED_16QAM:-3}"
Q64_WANTED="${SEED_64QAM:-2}"
ATTEMPTS="${SEED_ATTEMPTS:-40}"

say "Searching for admissible pairs (probing up to ${ATTEMPTS} at random)"
PAIRS="$(TWIN_URL="${TWIN_URL}" \
  QPSK_WANTED="${QPSK_WANTED}" Q16_WANTED="${Q16_WANTED}" Q64_WANTED="${Q64_WANTED}" \
  ATTEMPTS="${ATTEMPTS}" SEED_RANDOM_SEED="${SEED_RANDOM_SEED:-}" \
  python3 "$(dirname "$0")/find_pairs.py")" \
  || die "pair search failed — is the twin up at ${TWIN_URL}?"

[ -n "${PAIRS}" ] || die "no admissible pairs found in ${ATTEMPTS} probes"

# Restore the adapter's configured default when we are done, however we exit:
# leaving it on whatever the last pair used would silently change what Act 2
# provisions next.
ORIGINAL_MODULATION="$(curl -sS "${ADAPTER_URL}/adapter/status" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["modulation-format"])' \
  2>/dev/null || echo DP-QPSK)"

restore_modulation() {
  curl -sS -X POST -H 'Content-Type: application/json' \
    -d "{\"modulation-format\":\"${ORIGINAL_MODULATION}\"}" \
    "${ADAPTER_URL}/adapter/modulation" >/dev/null 2>&1 || true
}
trap restore_modulation EXIT

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
while IFS=':' read -r a_city z_city modulation label; do
  [ -n "${a_city}" ] || continue

  a_port="$(port_for_node "${a_city}" 2>/dev/null)" || { warn "skipping ${a_city}: not found"; continue; }
  z_port="$(port_for_node "${z_city}" 2>/dev/null)" || { warn "skipping ${z_city}: not found"; continue; }

  # Tell the adapter which format to request for the next lightpath. ONOS has
  # no way to carry this, so the adapter holds it as policy.
  curl -sSf -X POST -H 'Content-Type: application/json' \
    -d "{\"modulation-format\":\"${modulation}\"}" \
    "${ADAPTER_URL}/adapter/modulation" >/dev/null \
    || { warn "skipping ${a_city} -> ${z_city}: adapter rejected ${modulation}"; continue; }

  before="$(curl -sS "${ADAPTER_URL}/adapter/status" \
    | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["onos-created-services"]))')"

  body="$(printf '{"priority":100,"timeout":0,"isPermanent":true,"deviceId":"%s",' "${DEVICE_ID}"
          printf '"treatment":{"instructions":[{"type":"OUTPUT","port":"%s"}]},' "${z_port}"
          printf '"selector":{"criteria":[{"type":"IN_PORT","port":"%s"}]}}' "${a_port}")"

  printf '  %-14s -> %-14s  port %2s -> %-2s  %-9s %s\n' \
    "${a_city}" "${z_city}" "${a_port}" "${z_port}" "${modulation}" "(${label})"

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
                 ends, info.get('total-fiber-km', 0),
                 info.get('modulation-format', '?'), m))
rows.sort(reverse=True)
for gsnr, ends, km, mod, m in rows:
    fmt = lambda v: ('%6.2f' % v) if isinstance(v, (int, float)) else '%6s' % '--'
    print('    %-30s %-9s %7.1f km  GSNR %s dB  OSNR %s dB  BER %.2e'
          % (ends, mod, km, fmt(m['gsnr-db']), fmt(m['osnr-db']), m['pre-fec-ber']))
"

cat <<EOF

${C_BOLD}Where to look now${C_RESET}

  ONOS GUI      http://localhost:8181/onos/ui   (onos / rocks)
                Devices  -> click the OLS row -> the ports icon in the panel
                Flows    -> one ADDED flow per admitted lightpath
  TwinLight UI  http://localhost:5173           lightpaths on the map, spectrum heat map
  Grafana       http://localhost:3000           OPM series, one per lightpath

EOF
