#!/bin/sh
# Regenerate the SPA's runtime config from the environment before nginx starts.
#
# The nginx base image runs every executable /docker-entrypoint.d/*.sh on
# container start, so this needs no custom ENTRYPOINT. It rewrites config.js
# (shipped with a localhost default) with the deployment's twin URL, letting a
# single prebuilt image be pointed anywhere via the TWIN_URL env var.
#
# NB: this URL is used by the visitor's *browser*, so it must be reachable from
# the host — the twin's published port (e.g. http://localhost:8080), not the
# compose service name.
set -eu

: "${TWIN_URL:=http://localhost:8080}"

config_file=/usr/share/nginx/html/config.js
cat > "$config_file" <<EOF
window.__TWIN_CONFIG__ = { baseUrl: "${TWIN_URL}" };
EOF

echo "twin-config: Digital Twin URL set to ${TWIN_URL}"
