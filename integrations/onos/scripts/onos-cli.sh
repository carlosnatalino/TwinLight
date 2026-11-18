#!/usr/bin/env bash
# Open the ONOS Karaf CLI, or run one command in it.
#
#   onos-cli.sh                 interactive shell
#   onos-cli.sh devices         run a single command and exit
#   onos-cli.sh ports rest:172.28.0.10:8282
#
# Runs Karaf's own client inside the ONOS container (docker exec), so the host
# needs neither ssh nor sshpass, and the client is the one Karaf ships, which
# speaks its older SSHD's algorithms. The ONOS image has no ssh binary, which is
# why the `onos` wrapper script in it cannot be used; bin/client is Java.
#
# Without a TTY Karaf's client prints nothing and says only "Closed", so a
# one-shot command is given a TTY too (-t); its carriage returns and the
# "Logging in as" banner are stripped so the output can be piped.

. "$(dirname "$0")/lib.sh"

docker inspect "${ONOS_CONTAINER}" >/dev/null 2>&1 \
  || die "no container ${ONOS_CONTAINER} — run integrations/onos/scripts/demo-up.sh first"

CLIENT=(sh -c 'exec "$(ls -d /root/onos/apache-karaf-*/bin/client | head -1)" "$@"' client
        -h localhost -u "${ONOS_AUTH%%:*}" -p "${ONOS_AUTH#*:}")

if [ $# -eq 0 ]; then
  exec docker exec -it "${ONOS_CONTAINER}" "${CLIENT[@]}"
fi

docker exec -t "${ONOS_CONTAINER}" "${CLIENT[@]}" "$*" | tr -d '\r' | sed '1{/^Logging in as /d;}'
