"""Static safety contracts for the Stage 12.1 application image."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("published", [(8000, 5432, 5433), (18000, 15432, 15433)])
def test_compose_resolves_loopback_ports(published: tuple[int, int, int]) -> None:
    """Use Compose's parser without a daemon, local secrets, or override files."""

    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI required for Compose configuration validation")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("STMS_", "COMPOSE_"))
    }
    if published != (8000, 5432, 5433):
        environment.update(
            zip(
                ("STMS_APP_PORT", "STMS_POSTGRES_DEV_PORT", "STMS_POSTGRES_TEST_PORT"),
                map(str, published),
                strict=True,
            )
        )
    result = subprocess.run(
        [
            docker,
            "compose",
            "--env-file",
            os.devnull,
            "-f",
            str(ROOT / "compose.yaml"),
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, "Compose configuration validation failed"
    services = json.loads(result.stdout)["services"]
    for name, target, port in zip(
        ("app", "postgres-dev", "postgres-test"),
        (8000, 5432, 5432),
        published,
        strict=True,
    ):
        ports = services[name]["ports"]
        assert len(ports) == 1
        assert ports[0]["host_ip"] == "127.0.0.1"
        assert ports[0]["target"] == target
        assert str(ports[0]["published"]) == str(port)
        assert ports[0]["protocol"] == "tcp"
    assert "@postgres-dev:5432/" in services["app"]["environment"]["STMS_DATABASE_URL"]


def test_dockerfile_is_locked_minimal_non_root_and_fail_fast() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.startswith(
        "FROM ghcr.io/astral-sh/uv:0.12.10-python3.14-trixie-slim"
    )
    assert "uv sync --frozen --no-dev --no-install-project" in dockerfile
    assert "UV_CACHE_DIR=/tmp/stms-uv-cache" in dockerfile
    assert "UV_CACHE_DIR=/root/.cache/uv uv sync" in dockerfile
    assert "COPY pyproject.toml uv.lock ./" in dockerfile
    assert "COPY --chown=stms:stms alembic ./alembic" in dockerfile
    assert "COPY --chown=stms:stms app ./app" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "alembic upgrade head && exec" in dockerfile
    assert "uvicorn app.main:app --host 0.0.0.0 --port 8000" in dockerfile
    assert "reload" not in dockerfile.lower()
    assert "COPY ." not in dockerfile
    assert "STMS_ACCESS_TOKEN_SECRET" not in dockerfile
    assert "STMS_MODEL_API_KEY" not in dockerfile


def test_dockerignore_excludes_secrets_source_state_and_test_artifacts() -> None:
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {
        ".git",
        ".env",
        ".env.*",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "tests",
        "evals",
        "docs",
        ".codex",
        "attachments",
    } <= ignored


def test_compose_app_uses_only_internal_development_database() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    app_start = compose.index("\n  app:")
    postgres_start = compose.index("\n  postgres-dev:")
    app_section = compose[app_start:postgres_start]
    postgres_section = compose[postgres_start:]

    assert "  app:" in app_section
    assert "dockerfile: Dockerfile" in app_section
    assert "@postgres-dev:5432/" in app_section
    assert "condition: service_healthy" in app_section
    assert "127.0.0.1:${STMS_APP_PORT:-8000}:8000" in app_section
    assert "/health/ready" in app_section
    assert "restart: unless-stopped" in app_section
    assert "stop_grace_period: 30s" in app_section
    assert "STMS_ENV: development" in app_section
    assert "STMS_ACCESS_TOKEN_SECRET:" in app_section
    assert "local-compose-only-signing-secret" not in app_section
    assert "Insecure local-only fallback" not in app_section
    assert "STMS_MODEL_API_KEY" not in app_section
    assert "postgres-test" not in app_section
    assert "localhost:5432" not in app_section
    assert "127.0.0.1:5432" not in app_section
    assert "privileged:" not in app_section
    assert "/var/run/docker.sock" not in app_section
    assert "volumes:" not in app_section
    assert "postgres-dev-data:/var/lib/postgresql/data" in postgres_section
    assert "tmpfs:" in postgres_section
