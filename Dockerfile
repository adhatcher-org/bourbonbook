FROM docker.io/astral/uv:0.11.28 AS uv
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DATA_DIR=/data

COPY --from=uv /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY bourbonbook ./bourbonbook
COPY images ./images
COPY alembic.ini ./
COPY migrations ./migrations
COPY README.md ./
RUN groupadd --system app && useradd --system --gid app --home /app app \
    && mkdir -p /data && chown -R app:app /app /data

USER app
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["uv", "run", "--frozen", "--no-dev", "python", "-m", "bourbonbook.entrypoint"]
