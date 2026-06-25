# Multi-stage build for the T-API Twin Web UI (React + Vite SPA).
#
# Stage 1 compiles the static bundle; stage 2 serves it with nginx. The twin
# URL is NOT baked in: Vite would bind any VITE_* var at build time, but we
# want one image deployable against any twin, so the URL is injected at
# container start (docker-entrypoint.d/40-twin-config.sh -> config.js) from the
# TWIN_URL environment variable.
#
# Unlike the backend image, this needs no --platform pin: the node and nginx
# base images ship native arm64 + amd64 variants, so it builds natively on
# Apple Silicon.

# ---------------------------------------------------------------------
FROM node:22-alpine AS builder

WORKDIR /app

# Install deps first (cached unless the lockfile changes). `npm ci` requires
# package-lock.json and installs exactly what it pins.
COPY package.json package-lock.json ./
RUN npm ci

# Build the SPA (tsc -b && vite build) into /app/dist.
COPY . .
RUN npm run build


# ---------------------------------------------------------------------
FROM nginx:1.27-alpine

# SPA-aware site config (client-side routing fallback + cache headers).
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Static bundle.
COPY --from=builder /app/dist /usr/share/nginx/html

# Runtime config injector — the nginx base image runs executable
# /docker-entrypoint.d/*.sh before starting the server.
COPY docker-entrypoint.d/40-twin-config.sh /docker-entrypoint.d/40-twin-config.sh
RUN chmod +x /docker-entrypoint.d/40-twin-config.sh

# Browser-reachable twin URL; override per deployment.
ENV TWIN_URL="http://localhost:8080"

EXPOSE 80

# Default nginx CMD (from the base image) starts the server.
