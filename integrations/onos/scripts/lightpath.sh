#!/usr/bin/env bash
# Drive lightpath provisioning *from ONOS* and read the physics back from the twin.
#
#   lightpath.sh create <CityA> <CityZ>   push an ONOS flow rule on the OLS device;
#                                         the ols driver turns it into a T-API
#                                         connectivity-service on the twin
#   lightpath.sh list                     ONOS flows + twin services side by side
#   lightpath.sh delete <flow-id>         remove the flow; the driver DELETEs the service
#   lightpath.sh clear                    remove every flow this script created
#
# The ONOS→twin direction is the whole point: nothing here talks to the twin's
# connectivity API directly. Provisioning is entirely ONOS's decision; the twin
# is free to refuse it, and does.

. "$(dirname "$0")/lib.sh"

APP_ID="${APP_ID:-org.onosproject.rest}"

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

cmd_create() {
  [ $# -eq 2 ] || usage
  require_stack

  local a_city="$1" z_city="$2" a_port z_port
  a_port="$(port_for_node "${a_city}")" || die "unknown transceiver: ${a_city}"
  z_port="$(port_for_node "${z_city}")" || die "unknown transceiver: ${z_city}"
  [ "${a_port}" != "${z_port}" ] || die "source and destination are the same port"

  say "ONOS flow rule: port ${a_port} (${a_city}) -> port ${z_port} (${z_city}) on ${DEVICE_ID}"

  local body
  body="$(cat <<EOF
{
  "priority": 100,
  "timeout": 0,
  "isPermanent": true,
  "deviceId": "${DEVICE_ID}",
  "treatment": {"instructions": [{"type": "OUTPUT", "port": "${z_port}"}]},
  "selector": {"criteria": [{"type": "IN_PORT", "port": "${a_port}"}]}
}
EOF
)"

  local before after
  before="$(curl -sS "${ADAPTER_URL}/adapter/status")"
  onos_api POST "/onos/v1/flows/${DEVICE_ID}?appId=${APP_ID}" "${body}" >/dev/null \
    || die "ONOS rejected the flow rule"
  ok "flow rule accepted by ONOS"

  # TapiFlowRuleProgrammable POSTs to the adapter asynchronously
  # (CompletableFuture.supplyAsync), so the answer lands a moment later.
  say "Waiting for the twin's admission decision"
  sleep 6
  after="$(curl -sS "${ADAPTER_URL}/adapter/status")"

  printf '%s' "${after}" | python3 -c "
import json, sys
after = json.load(sys.stdin)
def modulation_of(svc):
    # T-API 2.6 puts modulation on the end-point, not on the service.
    spec_key = 'tapi-photonic-media:otsia-connectivity-service-end-point-spec'
    names = {'MT_DP-QPSK': 'DP-QPSK', 'MT_DP-QAM16': 'DP-16QAM',
             'MT_DP-QAM64': 'DP-64QAM'}
    for ep in svc.get('end-point') or []:
        for lpc in ep.get('layer-protocol-constraint') or []:
            for cfg in (lpc.get(spec_key) or {}).get('otsi-config') or []:
                mt = (cfg.get('modulation') or {}).get(
                    'standard-modulation-technique', '')
                if mt.split(':')[-1] in names:
                    return names[mt.split(':')[-1]]
    return '?'

before = json.loads(sys.argv[1])
new = set(after['onos-created-services']) - set(before['onos-created-services'])
rej_before = {(r['uuid'], r['at']) for r in before['recent-rejections']}
new_rej = [r for r in after['recent-rejections'] if (r['uuid'], r['at']) not in rej_before]

if new:
    for uuid in new:
        svc = after['onos-created-services'][uuid]['tapi-connectivity:connectivity-service']
        slot = svc.get('frequency-slot') or {}
        print('  ADMITTED by the twin')
        print('    service uuid  : %s' % svc['uuid'])
        print('    modulation    : %s' % modulation_of(svc))
        print('    centre freq   : %s THz' % slot.get('nominal-central-frequency'))
        print('    slot width    : %s GHz' % slot.get('slot-width'))
elif new_rej:
    for r in new_rej:
        print('  REFUSED by the twin (%s)' % r['reason'])
        print('    %s' % r['detail'])
    sys.exit(2)
else:
    print('  no adapter activity seen — check: docker logs twinlight-tapi-adapter')
    sys.exit(1)
" "${before}" || {
    rc=$?
    [ "${rc}" -eq 2 ] && {
      warn "ONOS asked, the twin said no — the flow rule will show as PENDING_ADD/FAILED"
      exit 0
    }
    exit "${rc}"
  }

  # Physics readback, straight from the twin.
  say "Twin optical performance monitoring"
  curl -sS "${ADAPTER_URL}/adapter/status" | python3 -c "
import json, sys, urllib.request
uuids = list(json.load(sys.stdin)['onos-created-services'])
for u in uuids:
    try:
        opm = json.load(urllib.request.urlopen('${TWIN_URL}/internal/opm/' + u))['measurements']
        info = json.load(urllib.request.urlopen('${TWIN_URL}/internal/services/' + u))
    except Exception as exc:
        print('    %s: %s' % (u[:8], exc)); continue
    # Every optical metric is null while a fiber on the path is cut.
    fmt = lambda v: ('%6.2f' % v) if isinstance(v, (int, float)) else '%6s' % '--'
    print('    %s  %6.1f km  GSNR %s dB  OSNR %s dB  Q %s dB  BER %.2e'
          % (u[:8], info.get('total-fiber-km', 0), fmt(opm['gsnr-db']),
             fmt(opm['osnr-db']), fmt(opm['q-factor-db']), opm['pre-fec-ber']))
"
}

cmd_list() {
  require_stack
  say "ONOS flow rules on ${DEVICE_ID}"
  onos_api GET "/onos/v1/flows/${DEVICE_ID}" | python3 -c "
import json, sys
flows = json.load(sys.stdin).get('flows', [])
mine = [f for f in flows if f.get('appId') == 'org.onosproject.rest']
if not mine:
    print('    (none)')
for f in mine:
    ins = [i for i in f['treatment']['instructions'] if i['type'] == 'OUTPUT']
    crit = [c for c in f['selector']['criteria'] if c['type'] == 'IN_PORT']
    print('    id=%-22s state=%-12s port %s -> %s'
          % (f['id'], f['state'],
             crit[0]['port'] if crit else '?', ins[0]['port'] if ins else '?'))
"
  say "Connectivity services on the twin (created via ONOS)"
  curl -sS "${ADAPTER_URL}/adapter/status" | python3 -c "
import json, sys
def modulation_of(svc):
    # T-API 2.6 puts modulation on the end-point, not on the service.
    spec_key = 'tapi-photonic-media:otsia-connectivity-service-end-point-spec'
    names = {'MT_DP-QPSK': 'DP-QPSK', 'MT_DP-QAM16': 'DP-16QAM',
             'MT_DP-QAM64': 'DP-64QAM'}
    for ep in svc.get('end-point') or []:
        for lpc in ep.get('layer-protocol-constraint') or []:
            for cfg in (lpc.get(spec_key) or {}).get('otsi-config') or []:
                mt = (cfg.get('modulation') or {}).get(
                    'standard-modulation-technique', '')
                if mt.split(':')[-1] in names:
                    return names[mt.split(':')[-1]]
    return '?'
svcs = json.load(sys.stdin)['onos-created-services']
if not svcs:
    print('    (none)')
for u, payload in svcs.items():
    s = payload['tapi-connectivity:connectivity-service']
    slot = s.get('frequency-slot') or {}
    print('    %s  %-9s  %s THz' % (u[:8], modulation_of(s),
                                    slot.get('nominal-central-frequency')))
"
}

cmd_delete() {
  [ $# -eq 1 ] || usage
  require_stack
  say "Removing flow ${1}"
  onos_api DELETE "/onos/v1/flows/${DEVICE_ID}/${1}" >/dev/null
  sleep 5
  ok "flow removed; the ols driver issued the T-API DELETE to the twin"
}

cmd_clear() {
  require_stack
  say "Removing every flow created by ${APP_ID}"
  local ids
  ids="$(onos_api GET "/onos/v1/flows/${DEVICE_ID}" | python3 -c "
import json, sys
for f in json.load(sys.stdin).get('flows', []):
    if f.get('appId') == 'org.onosproject.rest':
        print(f['id'])
")"
  if [ -z "${ids}" ]; then ok "nothing to remove"; return; fi
  for id in ${ids}; do
    onos_api DELETE "/onos/v1/flows/${DEVICE_ID}/${id}" >/dev/null && printf '     removed %s\n' "${id}"
  done
  sleep 5
  ok "done"
}

case "${1:-}" in
  create) shift; cmd_create "$@" ;;
  list)   shift; cmd_list "$@" ;;
  delete) shift; cmd_delete "$@" ;;
  clear)  shift; cmd_clear "$@" ;;
  *)      usage ;;
esac
