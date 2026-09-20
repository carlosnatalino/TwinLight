#!/usr/bin/env bash
# Tear the demo stack down.
#
#   demo-down.sh          stop and remove containers, keep volumes
#   demo-down.sh --purge  also remove prometheus/grafana volumes
#
# Note this stops the *whole* stack (twin, ui, prometheus, grafana) because the
# overlay shares its project with the root compose file.

. "$(dirname "$0")/lib.sh"

if [ "${1:-}" = "--purge" ]; then
  say "Stopping the stack and removing volumes"
  compose down -v
else
  say "Stopping the stack (volumes kept)"
  compose down
fi
ok "done"
