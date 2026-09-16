FROM ghcr.io/astral-sh/uv:0.12.10-python3.14-trixie-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_DEV=1 \
    UV_CACHE_DIR=/tmp/stms-uv-cache \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/stms-venv \
    PATH="/opt/stms-venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_CACHE_DIR=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

RUN groupadd --gid 10001 stms \
    && useradd --uid 10001 --gid stms --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin stms

COPY --chown=stms:stms alembic.ini ./
COPY --chown=stms:stms alembic ./alembic
COPY --chown=stms:stms app ./app

USER 10001:10001

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
