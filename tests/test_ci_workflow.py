"""Static safety contracts for the Task 12.2 GitHub Actions workflow."""

import re
from pathlib import Path

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ENV_EXAMPLE = ROOT / ".env.example"
ACCESS_TOKEN_SECRET = "STMS_ACCESS_TOKEN_SECRET"


def _job_sections(workflow: str) -> tuple[str, str]:
    """Return the two top-level job bodies without crossing their boundary."""

    quality, integration = workflow.split("  integration:\n", maxsplit=1)
    return quality.split("  quality:\n", maxsplit=1)[1], integration


def _job_env_value(job: str, variable: str) -> str | None:
    """Read one plain scalar from a job-scoped env block."""

    match = re.search(rf"^      {re.escape(variable)}:\s*(\S.*)$", job, re.MULTILINE)
    return match.group(1).strip("\"'") if match is not None else None


def test_ci_versions_permissions_concurrency_and_cache_are_bounded() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in workflow
    assert "cancel-in-progress: true" in workflow
    assert workflow.count("timeout-minutes:") == 2
    assert workflow.count('version: "0.12.10"') == 2
    assert workflow.count('python-version: "3.14"') == 2
    assert workflow.count("uv sync --locked --all-groups") == 2
    assert "uv lock --check" in workflow
    assert workflow.count("pyproject.toml\n            uv.lock") == 2
    assert workflow.count("persist-credentials: false") == 2
    assert "secrets." not in workflow
    actions = re.findall(r"uses: [^@\n]+@([^\s]+)", workflow)
    assert len(actions) == 4
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in actions)


def test_ordinary_job_is_offline_and_runs_every_quality_gate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    quality, _ = _job_sections(workflow)

    assert "uv run pytest\n" in quality
    assert "uv run ruff check ." in quality
    assert "uv run ruff format --check ." in quality
    assert "uv run mypy app tests" in quality
    assert "services:" not in quality
    assert "STMS_DATABASE_URL" not in quality
    assert "STMS_TEST_DATABASE_URL" not in quality
    assert ACCESS_TOKEN_SECRET not in quality
    assert "STMS_MODEL_API_KEY" not in quality
    assert "external_provider" not in quality


def test_integration_job_uses_only_guarded_dedicated_pgvector_database() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    _, integration = _job_sections(workflow)
    guard = integration.index("validate_migration_test_target")
    upgrade = integration.index("uv run alembic upgrade head")

    assert "pgvector/pgvector:0.8.6-pg17-bookworm" in integration
    assert "POSTGRES_DB: stms_test" in integration
    assert "POSTGRES_USER: stms_test" in integration
    assert "POSTGRES_HOST_AUTH_METHOD: trust" in integration
    assert "POSTGRES_PASSWORD" not in integration
    assert "127.0.0.1:5433/stms_test" in integration
    assert 'STMS_POSTGRES_TEST_PORT: "5433"' in integration
    assert guard < upgrade
    assert "uv run alembic current" in integration
    assert "uv run alembic heads" in integration
    assert "uv run alembic check" in integration
    assert (
        'uv run pytest -m "integration and not external_provider" tests/integration'
        in integration
    )
    assert "postgres-dev" not in integration
    assert "stms_dev" not in integration
    assert "STMS_MODEL_API_KEY" not in integration


def test_integration_job_has_only_a_runtime_valid_synthetic_test_secret() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    quality, integration = _job_sections(workflow)
    integration_secret = _job_env_value(integration, ACCESS_TOKEN_SECRET)

    assert ACCESS_TOKEN_SECRET not in quality
    assert workflow.count(f"{ACCESS_TOKEN_SECRET}:") == 1
    assert integration_secret is not None
    assert len(integration_secret) >= 32
    assert "test-only" in integration_secret
    assert "not-for-production" in integration_secret
    assert "${{" not in integration_secret
    assert integration_secret not in env_example
    assert "secrets." not in workflow

    settings = Settings.model_validate({"access_token_secret": integration_secret})
    assert settings.access_token_secret is not None
