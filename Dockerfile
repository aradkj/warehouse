# syntax=docker/dockerfile:1

# --------------------------------------------------------------------------
# Stage 1 - builder
# --------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install --no-install-recommends -y build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN python -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip \
 && pip install .


# --------------------------------------------------------------------------
# Stage 2 - runtime
# --------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    ARAD_ENVIRONMENT=production \
    ARAD_LOG_JSON=true \
    ARAD_API_PREFIX=/v1


RUN groupadd --system --gid 1001 arad \
 && useradd --system --uid 1001 --gid arad --create-home --shell /usr/sbin/nologin arad \
 && apt-get update \
 && apt-get install --no-install-recommends -y curl \
 && rm -rf /var/lib/apt/lists/*


COPY --from=builder /opt/venv /opt/venv


WORKDIR /app

COPY --chown=arad:arad alembic.ini ./
COPY --chown=arad:arad migrations ./migrations


USER arad

EXPOSE 8000


HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8000/healthz || exit 1


CMD ["uvicorn", "bootstrap.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]



# --------------------------------------------------------------------------
# Stage 3 - test
# --------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS test

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"


WORKDIR /app


COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY migrations ./migrations
COPY alembic.ini ./


RUN python -m venv /opt/venv \
 && pip install --upgrade --default-timeout=100 pip \
 && pip install --default-timeout=100 ".[dev]"


CMD ["pytest", "-q"]