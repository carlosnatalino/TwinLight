#!/usr/bin/env bash
# Inject and clear fiber failures in the twin, and show what ONOS sees.
#
#   fault.sh list                  fibers currently cut
#   fault.sh cut <fiber-uid>       cut a fiber
#   fault.sh cut-path <uuid>       cut the first fiber on a service's path
#   fault.sh heal <fiber-uid>      restore a fiber
#   fault.sh heal-all              restore every cut fiber
#
# Fault injection is a TwinLight /config/ operation, not a T-API one: T-API 2.6
# has a tapi-fault module, but TwinLight does not implement it (see
# docs/TAPI_COMPLIANCE.md), so ONOS cannot be notified through the standard
# path. What ONOS *does* see is the consequence — the twin starts reporting
# status="link-failed" and the OPM for every affected lightpath collapses.

. "$(dirname "$0")/lib.sh"

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

failed_links() { curl -sS "${TWIN_URL}/config/get" | python3 -c "
import json, sys
print('\n'.join(json.load(sys.stdin).get('failed_links', [])))
"; }

cmd_list() {
  say "Fibers currently cut"
  local links; links="$(failed_links)"
  if [ -z "${links}" ]; then printf '    (none)\n'; else printf '%s\n' "${links}" | sed 's/^/    /'; fi
}

set_failed() {
  # set_failed <uid> <true|false>
  local uid="$1" state="$2" body
  body="$(python3 -c "
import json, sys
print(json.dumps({'devices': {sys.argv[1]: {'failed': sys.argv[2] == 'true'}}}))
" "${uid}" "${state}")"
  curl -sS -X POST -H 'Content-Type: application/json' -d "${body}" \
    "${TWIN_URL}/config/set" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if 'detail' in d:
    sys.exit('    twin rejected the change: %s' % d['detail'])
inv = d.get('invalidated_services', {})
n = len({u for uuids in inv.values() for u in uuids})
print('    invalidated %d service baseline(s); %d fiber(s) now cut'
      % (n, len(d.get('failed_links', []))))
"
}

cmd_cut() {
  [ $# -eq 1 ] || usage
  say "Cutting fiber: $1"
  set_failed "$1" true
  show_impact
}

cmd_cut_path() {
  [ $# -eq 1 ] || usage
  local uid
  # /internal/services summarises the path at ROADM granularity (the twin keeps
  # the full element list internally, but only surfaces Roadm/Transceiver hops).
  # The /config failed flag applies to Fiber elements, so the fiber is resolved
  # by matching each consecutive ROADM pair against the fiber inventory, whose
  # UIDs are of the form "fiber (Austin -> San_Antonio)-...".
  uid="$(python3 -c "
import json, urllib.request, sys

svc = json.load(urllib.request.urlopen('${TWIN_URL}/internal/services/' + sys.argv[1]))
hops = [h['uid'] for h in (svc.get('hops') or []) if h.get('type') == 'Roadm']
if len(hops) < 2:
    sys.exit('service path has fewer than two ROADMs')

cities = [h.replace('roadm ', '') for h in hops]
devices = json.load(urllib.request.urlopen('${TWIN_URL}/config/get'))['devices']
fibers = [d['uid'] for d in devices if d.get('type') == 'Fiber']

def find(a, b):
    # The twin writes the arrow as U+2192; match on the endpoint names instead
    # of reproducing the exact separator.
    for f in fibers:
        if a in f and b in f and f.index(a) < f.index(b):
            return f
    return None

spans = [(a, b) for a, b in zip(cities, cities[1:])]
# Middle span: visibly mid-path, and away from the add/drop ends.
for a, b in [spans[len(spans) // 2]] + spans:
    hit = find(a, b) or find(b, a)
    if hit:
        print(hit)
        break
else:
    sys.exit('no fiber found for any span of %s' % ' -> '.join(cities))
" "$1")" || die "could not resolve a fiber on service $1"
  say "Cutting mid-path fiber of service ${1:0:8}: ${uid}"
  set_failed "${uid}" true
  show_impact
}

cmd_heal() {
  [ $# -eq 1 ] || usage
  say "Restoring fiber: $1"
  set_failed "$1" false
  show_impact
}

cmd_heal_all() {
  local links; links="$(failed_links)"
  [ -n "${links}" ] || { ok "no fibers are cut"; return; }
  say "Restoring all cut fibers"
  printf '%s\n' "${links}" | while IFS= read -r uid; do
    [ -n "${uid}" ] || continue
    printf '     restoring %s\n' "${uid}"
    set_failed "${uid}" false >/dev/null
  done
  show_impact
}

show_impact() {
  say "Twin OPM for ONOS-created lightpaths"
  curl -sS "${ADAPTER_URL}/adapter/status" | python3 -c "
import json, sys, urllib.request
uuids = list(json.load(sys.stdin)['onos-created-services'])
if not uuids:
    print('    (no ONOS-created lightpaths)')
for u in uuids:
    try:
        opm = json.load(urllib.request.urlopen('${TWIN_URL}/internal/opm/' + u))
    except Exception as exc:
        print('    %s: %s' % (u[:8], exc)); continue
    m = opm['measurements']
    status = opm.get('status', 'ok')
    # On a cut the twin nulls every optical metric and pins pre-FEC BER to 1.0,
    # so these must not be formatted as floats unconditionally.
    fmt = lambda v: ('%6.2f' % v) if isinstance(v, (int, float)) else '%6s' % '--'
    flag = '  <-- LINK FAILED' if status == 'link-failed' else ''
    print('    %s  GSNR %s dB  OSNR %s dB  BER %.2e  status=%s%s'
          % (u[:8], fmt(m['gsnr-db']), fmt(m['osnr-db']), m['pre-fec-ber'], status, flag))
"
  say "ONOS's view of the device"
  onos_api GET "/onos/v1/devices/${DEVICE_ID}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('    %s available=%s' % (d['id'], d['available']))
" 2>/dev/null || printf '    (ONOS not reachable)\n'
}

case "${1:-}" in
  list)     shift; cmd_list "$@" ;;
  cut)      shift; cmd_cut "$@" ;;
  cut-path) shift; cmd_cut_path "$@" ;;
  heal)     shift; cmd_heal "$@" ;;
  heal-all) shift; cmd_heal_all "$@" ;;
  *)        usage ;;
esac
