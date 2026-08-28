# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- web UI
#
# Pinned to the build host's architecture, not the target's. The output is static JS and
# CSS, identical whatever it was built on, so there is nothing to gain from running npm
# under emulation -- and a great deal to lose: an emulated arm64 npm build is minutes of
# wall clock for a byte-identical result.
FROM --platform=$BUILDPLATFORM node:22-slim AS web

WORKDIR /web

# Lockfile first, so a source edit does not reinstall node_modules.
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build


# ---------------------------------------------------------------- python deps
#
# This one does have to match the target: asyncpg, argon2-cffi and uvloop ship compiled
# wheels, so the venv is architecture-specific.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install .


# ---------------------------------------------------------------- runtime
FROM python:3.12-slim-bookworm

# Which commit produced this image. Stamped by CI and logged on every boot, so
# "did my repull actually take?" is answerable from the host without guessing at
# timestamps -- a question that came up twice before this existed.
ARG GIT_SHA=unknown

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WEB_DIR=/app/web \
    PODARIUM_BUILD=$GIT_SHA

# ffmpeg is for trimming silence and levelling loudness after a download. It is the
# largest thing in this image by some way (~100 MB unpacked); without it those settings
# report themselves unavailable and everything else works exactly as before.
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=web /web/dist ./web
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8044

# The stack runs this as 1026:100 to match the NFS share, so no USER is baked in here.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8044/healthz || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
