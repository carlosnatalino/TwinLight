#!/usr/bin/env bash
# Open the ONOS Karaf CLI, or run one command in it.
#
#   onos-cli.sh                 interactive shell
#   onos-cli.sh devices         run a single command and exit
#   onos-cli.sh ports rest:172.28.0.10:8282
#
# Password is "rocks" — you will be prompted unless sshpass is installed
# (brew install hudochenkov/sshpass/sshpass). Note that `docker exec` into the
# container is not an alternative: the ONOS image ships the onos client script
# but no ssh binary for it to call.
#
# ONOS regenerates its host key whenever the container is recreated, so this
# deliberately keeps the demo out of the caller's known_hosts.

. "$(dirname "$0")/lib.sh"

SSH_OPTS=(-p 8101
          -o StrictHostKeyChecking=no
          -o UserKnownHostsFile=/dev/null
          -o LogLevel=ERROR
          # ONOS 2.7 ships an older Karaf SSHD. Modern OpenSSH clients
          # (macOS 14+) disable these by default and the handshake fails
          # without re-enabling them.
          -o HostKeyAlgorithms=+ssh-rsa
          -o PubkeyAcceptedAlgorithms=+ssh-rsa
          -o KexAlgorithms=+diffie-hellman-group14-sha1)

if command -v sshpass >/dev/null 2>&1; then
  exec sshpass -p rocks ssh "${SSH_OPTS[@]}" onos@localhost "$@"
fi

printf '%sPassword is:%s rocks\n\n' "${C_BOLD}" "${C_RESET}"
exec ssh "${SSH_OPTS[@]}" onos@localhost "$@"
