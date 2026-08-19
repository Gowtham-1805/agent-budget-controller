# syntax=docker/dockerfile:1.7
#
# Multi-stage build for the Agent Budget Gateway.
#
# Dependencies are installed in a builder stage and the resulting virtualenv is
# copied into a clean runtime image, so build tooling never ships to production.
# The image runs as a non-root user, holds no secrets, and handles SIGTERM
# properly -- ECS sends it on every deploy, and a process that ignores it gets
# killed mid-request, which for this service means abandoned reservations.

# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependency metadata is copied first so the install layer is cached until the
# dependencies themselves change, not on every source edit.
COPY pyproject.toml ./
COPY apps/gateway/src/abc_gateway/__init__.py apps/gateway/src/abc_gateway/__init__.py

RUN pip install --no-cache-dir "hatchling==1.27.0" \
    && pip install --no-cache-dir ".[providers,observability]"

COPY apps ./apps
COPY pricing ./pricing
RUN pip install --no-cache-dir --no-deps .

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="agent-budget-controller" \
      org.opencontainers.image.description="Financial authorization gateway for AI inference"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    ABC_ENVIRONMENT=production

RUN groupadd --system --gid 1001 abc \
    && useradd --system --uid 1001 --gid abc --no-create-home abc

# curl is used by the container healthcheck below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --chown=abc:abc apps/gateway/src /app/src
COPY --chown=abc:abc pricing /app/pricing

WORKDIR /app
ENV PYTHONPATH=/app/src \
    ABC_PRICE_CATALOG_PATH=/app/pricing/catalog.json

USER abc
EXPOSE 8000

# Liveness only: this must not fail because a dependency is down, or the
# container gets killed and restarted for a problem a restart cannot fix.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/healthz || exit 1

# `exec` form so uvicorn is PID 1 and receives SIGTERM directly. Without it a
# shell would swallow the signal and ECS would eventually SIGKILL the task
# mid-request, leaving reservations to be swept rather than settled.
CMD ["uvicorn", "abc_gateway.main:create_app", \
     "--factory", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--timeout-graceful-shutdown", "25", \
     "--no-server-header", \
     "--access-log"]
