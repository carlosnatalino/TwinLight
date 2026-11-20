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
# needs 8.5 dB GSNR, DP-16QAM 14.5 and DP-64QAM 20.5, plus the twin's
# rmsa.qot_margin_db on top. So the high-order formats are asked for on the short hops that can
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
# and DP-64QAM 20.5, each plus rmsa.qot_margin_db, which find_pairs.py reads
# from the twin at the start of the search (the margin is a runtime setting;
# a twin that cannot say stops the search). On the bundled CORONET
# topology only metro-distance hops reach DP-64QAM, which is the point worth
# showing -- the twin's answer depends on the format, not just the route.
#
# A probe is not the admission decision, though: the probe's GSNR includes the
# twin's time-varying transient terms and admission's does not, so a pair a
# few tenths of a dB above a threshold in the probe can be refused (see
# find_pairs.py). The search therefore also finds a spare pair per format;
# a spare is provisioned only when a pair of its format is refused. A refused
# create leaves its ONOS flow rule in PENDING_ADD, retried for ever, so the
# script removes that flow and says so: ONOS ends with one ADDED flow per
# lightpath and nothing else.
#
#   SEED_QPSK / SEED_16QAM / SEED_64QAM   how many of each to look for
#   SEED_SPARES                           extra pairs per format, used on a refusal
#   SEED_ATTEMPTS                         probe budget
#   SEED_RANDOM_SEED                      fixes the order pairs are drawn in
#   SEED_PAIRS_FILE                       provision these pairs instead of searching:
#                                         lines as find_pairs.py prints them, e.g.
#                                         a saved search, to replay a seeding
QPSK_WANTED="${SEED_QPSK:-3}"
Q16_WANTED="${SEED_16QAM:-3}"
Q64_WANTED="${SEED_64QAM:-2}"
SPARES="${SEED_SPARES:-1}"
ATTEMPTS="${SEED_ATTEMPTS:-40}"

# Cleared before the search, so the probes see the line the creates will.
if [ "${1:-}" = "--reset" ]; then
  say "Clearing existing ONOS flows"
  "$(dirname "$0")/lightpath.sh" clear >/dev/null 2>&1 || true
fi

if [ -n "${SEED_PAIRS_FILE:-}" ]; then
  say "Provisioning the pairs in ${SEED_PAIRS_FILE} (no search)"
  PAIRS="$(cat "${SEED_PAIRS_FILE}")" || die "cannot read ${SEED_PAIRS_FILE}"
  [ -n "${PAIRS}" ] || die "${SEED_PAIRS_FILE} lists no pairs"
else
  say "Searching for admissible pairs (probing up to ${ATTEMPTS} at random${SEED_RANDOM_SEED:+, seed ${SEED_RANDOM_SEED}})"
  # find_pairs.py says on stderr which margin it read, or why it could not.
  PAIRS="$(TWIN_URL="${TWIN_URL}" \
    QPSK_WANTED="${QPSK_WANTED}" Q16_WANTED="${Q16_WANTED}" Q64_WANTED="${Q64_WANTED}" \
    SPARES="${SPARES}" ATTEMPTS="${ATTEMPTS}" SEED_RANDOM_SEED="${SEED_RANDOM_SEED:-}" \
    python3 "$(dirname "$0")/find_pairs.py")" \
    || die "pair search failed (the reason is above) — is the twin up at ${TWIN_URL}?"
  [ -n "${PAIRS}" ] || die "no admissible pairs found in ${ATTEMPTS} probes"
fi

wanted_of() {
  case "$1" in
    DP-QPSK) echo "${QPSK_WANTED}" ;; DP-16QAM) echo "${Q16_WANTED}" ;;
    DP-64QAM) echo "${Q64_WANTED}" ;; *) echo 0 ;;
  esac
}
ADMITTED_FORMATS=""   # one line per admitted lightpath, its format
admitted_of() { printf '%s' "${ADMITTED_FORMATS}" | grep -cx -- "$1" || true; }
service_count() {
  curl -sS "${ADAPTER_URL}/adapter/status" \
    | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["onos-created-services"]))'
}
# The newest refusal the adapter recorded, as "<uuid> <time>", or "none".
last_rejection() {
  curl -sS "${ADAPTER_URL}/adapter/status" | python3 -c '
import json, sys
r = json.load(sys.stdin).get("recent-rejections") or []
print("%s %s" % (r[-1]["uuid"], r[-1]["at"]) if r else "none")'
}
rejection_detail() {
  curl -sS "${ADAPTER_URL}/adapter/status" | python3 -c '
import json, sys
r = json.load(sys.stdin).get("recent-rejections") or []
print(("%s: %s" % (r[-1]["reason"], r[-1]["detail"]))[:160] if r else "no reason recorded")'
}
flow_present() {
  onos_api GET "/onos/v1/flows/${DEVICE_ID}" | python3 -c '
import json, sys
sys.exit(0 if any(f.get("id") == sys.argv[1] for f in json.load(sys.stdin).get("flows", [])) else 1)
' "$1"
}
flow_gone() { ! flow_present "$1"; }

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

say "Seeding lightpaths through ONOS"
printf '\n'

created=0
refused=0

# Iterate line by line: the labels contain spaces, so an unquoted `for` over
# $PAIRS would split each description into separate loop iterations.
while IFS=':' read -r a_city z_city modulation label; do
  [ -n "${a_city}" ] || continue
  # A spare is for a refusal: skip it once its format has what was asked for.
  [ "$(admitted_of "${modulation}")" -lt "$(wanted_of "${modulation}")" ] || continue

  a_port="$(port_for_node "${a_city}" 2>/dev/null)" || { warn "skipping ${a_city}: not found"; continue; }
  z_port="$(port_for_node "${z_city}" 2>/dev/null)" || { warn "skipping ${z_city}: not found"; continue; }
  # find_pairs.py gives each pair its own A-end; a flow left from before (no
  # --reset) may still hold one, and ONOS would replace that flow.
  if [ -n "$(onos_flow_on_port "${a_port}")" ]; then
    warn "skipping ${a_city} -> ${z_city}: port ${a_port} already carries a flow"; continue
  fi

  # Tell the adapter which format to request for the next lightpath. ONOS has
  # no way to carry this, so the adapter holds it as policy.
  curl -sSf -X POST -H 'Content-Type: application/json' \
    -d "{\"modulation-format\":\"${modulation}\"}" \
    "${ADAPTER_URL}/adapter/modulation" >/dev/null \
    || { warn "skipping ${a_city} -> ${z_city}: adapter rejected ${modulation}"; continue; }

  before="$(service_count)"
  rejected_before="$(last_rejection)"

  body="$(printf '{"priority":100,"timeout":0,"isPermanent":true,"deviceId":"%s",' "${DEVICE_ID}"
          printf '"treatment":{"instructions":[{"type":"OUTPUT","port":"%s"}]},' "${z_port}"
          printf '"selector":{"criteria":[{"type":"IN_PORT","port":"%s"}]}}' "${a_port}")"

  printf '  %-14s -> %-14s  port %2s -> %-2s  %-9s %s\n' \
    "${a_city}" "${z_city}" "${a_port}" "${z_port}" "${modulation}" "(${label})"

  onos_api POST "/onos/v1/flows/${DEVICE_ID}?appId=org.onosproject.rest" "${body}" >/dev/null \
    || { warn "    ONOS rejected the flow rule"; continue; }

  # The driver POSTs asynchronously; GNPy propagation for a long path takes a
  # few seconds, so poll rather than sleeping a fixed amount. The adapter
  # records a refusal as soon as the twin answers 409.
  outcome=""
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    sleep 3
    if [ "$(service_count)" -gt "${before}" ]; then outcome=admitted; break; fi
    if [ "$(last_rejection)" != "${rejected_before}" ]; then outcome=refused; break; fi
  done

  if [ "${outcome}" = admitted ]; then
    created=$((created + 1))
    ADMITTED_FORMATS="${ADMITTED_FORMATS}${modulation}
"
    continue
  fi
  refused=$((refused + 1))
  if [ "${outcome}" = refused ]; then
    printf '    refused by the twin (%s)\n' "$(rejection_detail)"
  else
    printf '    no answer from the twin within 60 s\n'
  fi
  # ONOS would retry the flow for ever in PENDING_ADD: remove it.
  flow="$(onos_flow_on_port "${a_port}")"
  [ -n "${flow}" ] || die "the flow on port ${a_port} is not in ONOS; check ${ONOS_URL}/onos/ui"
  onos_api DELETE "/onos/v1/flows/${DEVICE_ID}/${flow}" >/dev/null \
    && wait_for "ONOS to drop flow ${flow}" 60 flow_gone "${flow}" \
    || die "could not remove flow ${flow}: ONOS keeps it in PENDING_ADD"
  printf '    removed its flow %s from ONOS\n' "${flow}"
done <<EOF
${PAIRS}
EOF

printf '\n'
ok "${created} lightpath(s) admitted, ${refused} refused (their flows removed)"
for fmt in DP-64QAM DP-16QAM DP-QPSK; do
  [ "$(admitted_of "${fmt}")" -ge "$(wanted_of "${fmt}")" ] \
    || warn "${fmt}: $(admitted_of "${fmt}") of $(wanted_of "${fmt}") admitted"
done

say "Twin optical performance monitoring"
curl -sS "${ADAPTER_URL}/adapter/status" | python3 -c "
import json, sys
sys.path.insert(0, '${SCRIPT_DIR}')
from tapi_fields import twin_urlopen
svcs = json.load(sys.stdin)['onos-created-services']
rows = []
for u in svcs:
    try:
        opm = json.load(twin_urlopen('${TWIN_URL}/internal/opm/' + u))
        info = json.load(twin_urlopen('${TWIN_URL}/internal/services/' + u))
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

  ONOS GUI      ${ONOS_URL}/onos/ui   (onos / rocks)
                Devices  -> click the OLS row -> the ports icon in the panel
                Flows    -> one ADDED flow per admitted lightpath
  TwinLight UI  ${UI_URL}           lightpaths on the map, spectrum heat map
  Grafana       ${GRAFANA_URL}           OPM series, one per lightpath

EOF
