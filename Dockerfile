# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.13.13-slim-bookworm@sha256:355bfa66770995d7e9a0da4b3473b44d0cb451f6b56f5615ad9c39e3c4eca03f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE /app/
RUN uv sync --frozen --no-dev --no-install-project
COPY src /app/src
RUN uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS service
LABEL org.opencontainers.image.title="kiro-headless-service" \
      org.opencontainers.image.description="Independent durable HTTP service" \
      org.opencontainers.image.licenses="MIT"
ENV PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 KHS_CONFIG=/config/service.json \
    KHS_KIRO_CONFIG_DIR=/config/kiro
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini /app/
COPY migrations /app/migrations
COPY examples/mcp_echo.py /app/examples/mcp_echo.py
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["kiro-service"]
CMD ["api"]

# Public deployment image: the CLI is acquired on the deployer's machine.
FROM service AS runtime
USER root
RUN mkdir -p /var/lib/kiro && chown 10001:10001 /var/lib/kiro
ENV KHS_CLI_BOOTSTRAP=download KHS_CLI_CACHE_DIR=/var/lib/kiro
USER 10001:10001
