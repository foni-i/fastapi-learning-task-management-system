"""Real PostgreSQL round-trip checks for Agent product business records."""

from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import DateTime, inspect
from sqlalchemy.engine import URL, Engine
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.models import AgentApproval, AgentRun, AgentThread, User
from tests.integration.conftest import validate_migration_test_target

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PRE_AGENT_RECORD_REVISION = "6e2f9a4c1b73"
AGENT_RECORD_REVISION = "21ec26a7c672"
AGENT_TABLES = {"agent_threads", "agent_runs", "agent_approvals"}


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def explicit_index_names(inspector: Inspector, table_name: str) -> set[str]:
    """Exclude PostgreSQL indexes already represented as unique constraints."""

    return {
        str(item["name"])
        for item in inspector.get_indexes(table_name)
        if item.get("duplicates_constraint") is None
    }


def assert_agent_record_contract(engine: Engine) -> None:
    inspector = inspect(engine)
    assert set(inspector.get_table_names(schema="public")) >= AGENT_TABLES

    assert set(item["name"] for item in inspector.get_columns("agent_threads")) == {
        "id",
        "user_id",
        "goal_summary",
        "status",
        "created_at",
        "updated_at",
    }
    assert inspector.get_pk_constraint("agent_threads")["name"] == "pk_agent_threads"
    assert {item["name"] for item in inspector.get_foreign_keys("agent_threads")} == {
        "fk_agent_threads_user_id_users"
    }
    assert {
        item["name"] for item in inspector.get_unique_constraints("agent_threads")
    } == {"uq_agent_threads_id_user_id"}
    assert {
        item["name"] for item in inspector.get_check_constraints("agent_threads")
    } == {"ck_agent_threads_goal_summary_not_blank", "ck_agent_threads_status"}
    assert explicit_index_names(inspector, "agent_threads") == {
        "ix_agent_threads_user_id",
        "ix_agent_threads_user_status",
    }

    run_columns = {item["name"]: item for item in inspector.get_columns("agent_runs")}
    assert set(run_columns) == {
        "id",
        "thread_id",
        "user_id",
        "status",
        "current_node",
        "summary",
        "error_code",
        "prompt_version",
        "model_round_count",
        "provider_attempt_count",
        "tool_call_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_ms",
        "created_at",
        "updated_at",
    }
    for name in ("created_at", "updated_at"):
        assert cast(DateTime, run_columns[name]["type"]).timezone is True
    assert inspector.get_pk_constraint("agent_runs")["name"] == "pk_agent_runs"
    assert {item["name"] for item in inspector.get_foreign_keys("agent_runs")} == {
        "fk_agent_runs_thread_id_user_id_agent_threads"
    }
    assert {
        item["name"] for item in inspector.get_unique_constraints("agent_runs")
    } == {"uq_agent_runs_id_user_id"}
    assert {item["name"] for item in inspector.get_check_constraints("agent_runs")} == {
        "ck_agent_runs_status",
        "ck_agent_runs_current_node",
        "ck_agent_runs_error_code",
        "ck_agent_runs_metrics_nonnegative",
        "ck_agent_runs_total_tokens",
    }
    assert explicit_index_names(inspector, "agent_runs") == {
        "ix_agent_runs_thread_id",
        "ix_agent_runs_user_id",
        "ix_agent_runs_user_status",
    }

    assert set(item["name"] for item in inspector.get_columns("agent_approvals")) == {
        "id",
        "run_id",
        "user_id",
        "revision",
        "proposal_fingerprint",
        "decision",
        "feedback",
        "decided_at",
        "created_at",
        "updated_at",
    }
    assert inspector.get_pk_constraint("agent_approvals")["name"] == (
        "pk_agent_approvals"
    )
    assert {item["name"] for item in inspector.get_foreign_keys("agent_approvals")} == {
        "fk_agent_approvals_run_id_user_id_agent_runs"
    }
    assert {
        item["name"] for item in inspector.get_unique_constraints("agent_approvals")
    } == {"uq_agent_approvals_run_id_revision"}
    assert {
        item["name"] for item in inspector.get_check_constraints("agent_approvals")
    } == {
        "ck_agent_approvals_revision",
        "ck_agent_approvals_proposal_fingerprint",
        "ck_agent_approvals_decision",
        "ck_agent_approvals_decision_state",
    }
    assert explicit_index_names(inspector, "agent_approvals") == {
        "ix_agent_approvals_run_id",
        "ix_agent_approvals_user_id",
    }


def test_agent_record_migration_upgrade_downgrade_reupgrade(
    migration_test_database_url: URL,
    integration_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = migration_test_database_url.render_as_string(hide_password=False)
    config = Config(str(ROOT / "alembic.ini"))
    monkeypatch.setenv("STMS_DATABASE_URL", target)
    get_settings.cache_clear()
    try:
        validate_migration_test_target(target)
        command.downgrade(config, PRE_AGENT_RECORD_REVISION)
        assert current_revision(integration_engine) == PRE_AGENT_RECORD_REVISION
        tables = set(inspect(integration_engine).get_table_names())
        assert AGENT_TABLES.isdisjoint(tables)
        assert {"users", "projects", "tasks"} <= tables

        command.upgrade(config, AGENT_RECORD_REVISION)
        assert current_revision(integration_engine) == AGENT_RECORD_REVISION
        assert_agent_record_contract(integration_engine)

        command.downgrade(config, PRE_AGENT_RECORD_REVISION)
        assert current_revision(integration_engine) == PRE_AGENT_RECORD_REVISION
        tables = set(inspect(integration_engine).get_table_names())
        assert AGENT_TABLES.isdisjoint(tables)
        assert {"users", "projects", "tasks"} <= tables

        command.upgrade(config, AGENT_RECORD_REVISION)
        assert_agent_record_contract(integration_engine)
    finally:
        command.upgrade(config, "head")
        get_settings.cache_clear()


def _new_run(session: Session) -> tuple[User, AgentThread, AgentRun]:
    user = User(
        email=f"agent-record-{uuid4()}@example.com",
        password_hash="synthetic-test-hash",
    )
    session.add(user)
    session.flush()
    thread = AgentThread(user_id=user.id, goal_summary="Test recovery records")
    session.add(thread)
    session.flush()
    run = AgentRun(
        thread_id=thread.id,
        user_id=user.id,
        prompt_version="study-plan-v1",
    )
    session.add(run)
    session.flush()
    return user, thread, run


def test_postgresql_enforces_thread_run_owner_consistency(
    integration_engine: Engine,
) -> None:
    with Session(integration_engine) as session:
        first_user, thread, _ = _new_run(session)
        other = User(
            email=f"agent-record-other-{uuid4()}@example.com",
            password_hash="synthetic-test-hash",
        )
        session.add(other)
        session.flush()
        session.add(
            AgentRun(
                thread_id=thread.id,
                user_id=other.id,
                prompt_version="study-plan-v1",
            )
        )
        with pytest.raises(IntegrityError) as error:
            session.flush()
        assert first_user.id != other.id
        diagnostic = getattr(error.value.orig, "diag", None)
        assert getattr(diagnostic, "constraint_name", None) == (
            "fk_agent_runs_thread_id_user_id_agent_threads"
        )
        session.rollback()


def test_postgresql_enforces_approval_revision_and_state_constraints(
    integration_engine: Engine,
) -> None:
    with Session(integration_engine) as session:
        user, _, run = _new_run(session)
        session.add(
            AgentApproval(
                run_id=run.id,
                user_id=user.id,
                revision=0,
                proposal_fingerprint="a" * 64,
            )
        )
        session.flush()
        session.add(
            AgentApproval(
                run_id=run.id,
                user_id=user.id,
                revision=0,
                proposal_fingerprint="b" * 64,
            )
        )
        with pytest.raises(IntegrityError) as error:
            session.flush()
        diagnostic = getattr(error.value.orig, "diag", None)
        assert getattr(diagnostic, "constraint_name", None) == (
            "uq_agent_approvals_run_id_revision"
        )
        session.rollback()

    invalid_cases = (
        {"revision": 3, "proposal_fingerprint": "a" * 64},
        {"revision": 0, "proposal_fingerprint": "invalid"},
        {
            "revision": 0,
            "proposal_fingerprint": "a" * 64,
            "decision": "APPROVED",
        },
    )
    for values in invalid_cases:
        with Session(integration_engine) as session:
            user, _, run = _new_run(session)
            session.add(AgentApproval(run_id=run.id, user_id=user.id, **values))
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()
