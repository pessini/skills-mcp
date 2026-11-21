# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ghcr.io/astral-sh/uv:0.13.9 AS uv
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 PORT=8000 SKILLS_ROOT=/app/skills
RUN groupadd --gid 10001 skills \
    && useradd --uid 10001 --gid skills --no-create-home --shell /usr/sbin/nologin skills
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY skills_mcp ./skills_mcp
COPY skills /app/skills
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + '/healthz', timeout=3).close()"]
ENTRYPOINT ["python", "-m", "skills_mcp"]
