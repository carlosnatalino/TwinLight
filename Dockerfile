# Multi-stage build for the T-API Network Digital Twin.
#
# The image bundles the FastAPI backend + gRPC server + the configured
# physical-layer backend (GNPy by default; EGN selectable via
# --physics-backend egn at run time, no extra install needed).
#
# Build context = repository root. The .dockerignore excludes venv/,
# tapi-twin-ui/, related-projects/, git history, snapshots, and the
# (sizeable) frontend node_modules so the context stays small.
#
# Both stages pin --platform=linux/amd64 because one of gnpy's hard
# transitive deps (oopt-gnpy-libyang) only publishes manylinux wheels
# for x86_64; on aarch64 pip falls back to an sdist that needs
# libyang2 + cmake + ninja to compile. Running the image under
# Rosetta/qemu on an Apple Silicon host is the cheaper trade.

ARG TARGET_PLATFORM=linux/amd64
FROM --platform=${TARGET_PLATFORM} python:3.12-slim AS builder

# Build deps for any wheel that compiles on Linux (scipy/numpy ship
# manylinux wheels, but pip still wants gcc available for sdist
# fallbacks on less-common architectures).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Match the runtime layout (WORKDIR /app + src at /app/src) so the
# console-script entry point baked into the venv references the same
# absolute path that the runtime stage will mount. Avoids a redundant
# `pip install -e .` re-install on the runtime side.
WORKDIR /app

# Install into a self-contained venv we can copy into the runtime
# image. Source must be present before `pip install .` so setuptools
# can discover the package under src/ (per pyproject.toml's
# [tool.setuptools.packages.find]).
COPY pyproject.toml ./
COPY src ./src
COPY examples ./examples
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -e .


# ---------------------------------------------------------------------
FROM --platform=${TARGET_PLATFORM} python:3.12-slim

# Non-root user — the twin is a long-running server, no need for root.
RUN useradd --create-home --shell /bin/bash twin

# Copy the prepared venv + application tree from the builder.
COPY --from=builder --chown=twin:twin /opt/venv /opt/venv
COPY --from=builder --chown=twin:twin /app /app

WORKDIR /app
USER twin
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Snapshot directory is bind-mounted from the host so /admin/snapshot
# output survives container restarts.
RUN mkdir -p /app/snapshots
VOLUME ["/app/snapshots"]

EXPOSE 8080 50051

# Default config is the CORONET CONUS scenario — the topology + the
# equipment file ship inside examples/gnpy-data/ so the first-run demo
# needs no host bind-mounts. Override with:
#   docker run … tapi-twin --config /path/to/your.yaml --rest-host 0.0.0.0
CMD ["tapi-twin", "--config", "/app/examples/coronet_conus_config.yaml", \
     "--rest-host", "0.0.0.0"]
