# Multi-stage build for the TwinLight optical network digital twin.
#
# The image bundles the FastAPI backend + gRPC server + the configured
# physical-layer backend (GNPy by default; EGN selectable via
# --physics-backend egn at run time, no extra install needed).
#
# Runtime dependencies only: the [dev] extra (pytest, ruff, mypy and their
# trees) is never installed, the compiler toolchain stays in the builder
# stage, and the runtime image receives only the finished venv plus the
# example scenarios — no source tree, no build cache, no test fixtures.
#
# Build context = repository root. The .dockerignore excludes .venv/,
# twinlight-ui/, related-projects/, git history, snapshots, docs and tests
# so the context stays small.
#
# Both stages pin --platform=linux/amd64 because one of gnpy's hard
# transitive deps (oopt-gnpy-libyang) only publishes manylinux wheels
# for x86_64; on aarch64 pip falls back to an sdist that needs
# libyang2 + cmake + ninja to compile. Running the image under
# Rosetta/qemu on an Apple Silicon host is the cheaper trade.

ARG TARGET_PLATFORM=linux/amd64
FROM --platform=${TARGET_PLATFORM} python:3.12-slim AS builder

# uv resolves and installs the dependency tree an order of magnitude
# faster than pip, and is the tool contributors use locally too
# (see CONTRIBUTING.md). Copied from the official distroless image so
# the builder needs no extra package-manager step.
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# Build deps for any wheel that has no prebuilt binary for this platform.
# Confined to the builder stage — the runtime image below never sees them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Non-editable install of the runtime dependency set into a self-contained
# venv that the runtime stage copies wholesale. Source must be present
# before the install so setuptools can discover the package under src/
# (per pyproject.toml's [tool.setuptools.packages.find]).
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
ENV UV_LINK_MODE=copy \
    UV_NO_CACHE=1
RUN uv venv /opt/venv --python 3.12 \
    && VIRTUAL_ENV=/opt/venv uv pip install --no-cache .

# The GNPy reference topologies used by examples/*.yaml are owned by
# oopt-gnpy and are not redistributed in this repository, so examples/
# arrives here without them. Copy them out of the gnpy distribution
# installed above, so the shipped scenarios run with no host bind-mount.
COPY examples ./examples
RUN /opt/venv/bin/twinlight-fetch-examples --dest /build/examples/gnpy-data

# Byte-code caches and the packaging metadata of the build are dead weight
# in the runtime image.
RUN find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} + \
    && rm -rf /build/src /build/pyproject.toml


# ---------------------------------------------------------------------
FROM --platform=${TARGET_PLATFORM} python:3.12-slim

# Non-root user — the twin is a long-running server, no need for root.
# /app and the snapshot directory are created here, while still root, and
# handed to that user: a COPY would otherwise leave the implicitly created
# /app owned by root and the server unable to write snapshots into it.
RUN useradd --create-home --shell /bin/bash twinlight \
    && mkdir -p /app/snapshots \
    && chown -R twinlight:twinlight /app

# Only two things are needed at run time: the prepared venv (which
# contains the installed twinlight package) and the example scenarios.
COPY --from=builder --chown=twinlight:twinlight /opt/venv /opt/venv
COPY --from=builder --chown=twinlight:twinlight /build/examples /app/examples

WORKDIR /app
USER twinlight
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Snapshot directory is bind-mounted from the host so /admin/snapshot
# output survives container restarts.
VOLUME ["/app/snapshots"]

EXPOSE 8080 50051

# Default config is the CORONET CONUS scenario — its topology and
# equipment files were provisioned into examples/gnpy-data/ during the
# build, so the first-run demo needs no host bind-mounts. Override with:
#   docker run … twinlight --config /path/to/your.yaml --rest-host 0.0.0.0
CMD ["twinlight", "--config", "/app/examples/coronet_conus_config.yaml", \
     "--rest-host", "0.0.0.0"]
