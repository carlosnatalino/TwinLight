# Multi-stage build for the T-API Network Digital Twin.
#
# The image bundles the FastAPI backend + gRPC server + the configured
# physical-layer backend (GNPy by default; EGN selectable via
# --physics-backend egn at run time, no extra install needed).
#
# Build context = repository root. The .dockerignore excludes venv/,
# tapi-twin-ui/, related-projects/, git history, snapshots, and the
# (sizeable) frontend node_modules — keep the image small.

FROM python:3.12-slim AS builder

# Build deps for any wheel that compiles on Linux (scipy etc. have wheels
# on PyPI but pip still wants gcc available for sdist fallbacks).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
# Install Python deps into a self-contained venv we can copy into the
# runtime image. Two-step copy (pyproject first, then sources) so the
# dependency layer caches across source-only edits.
COPY pyproject.toml ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir .

COPY src ./src
RUN /opt/venv/bin/pip install --no-cache-dir --no-deps -e .


# ---------------------------------------------------------------------
FROM python:3.12-slim

# Non-root user — the twin is a long-running server and doesn't need
# root inside the container.
RUN useradd --create-home --shell /bin/bash twin

# Copy the prepared venv from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Application code + example configs / topologies.
WORKDIR /app
COPY --chown=twin:twin src ./src
COPY --chown=twin:twin pyproject.toml ./
COPY --chown=twin:twin examples ./examples
# Re-install in editable mode so the venv's entry point (tapi-twin)
# resolves /app/src — keeps the image debuggable (edit-mount /app/src
# from the host and the change is live).
RUN /opt/venv/bin/pip install --no-cache-dir --no-deps -e .

USER twin
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Snapshot directory is bind-mounted from the host so /admin/snapshot
# output survives container restarts.
RUN mkdir -p /app/snapshots
VOLUME ["/app/snapshots"]

EXPOSE 8080 50051

# Default config is the CORONET CONUS scenario — it ships with the
# topology + equipment files inside examples/gnpy-data/, so no host
# bind-mounts are required for a first-run demo. Override with:
#   docker run … tapi-twin --config /path/to/your.yaml
CMD ["tapi-twin", "--config", "/app/examples/coronet_conus_config.yaml", \
     "--rest-host", "0.0.0.0"]
