"""Connection-free contracts for product-owned Agent audit models."""

from typing import cast

from sqlalchemy import CheckConstraint, DateTime, Float, Integer, String, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import DefaultClause, ForeignKeyConstraint, UniqueConstraint

from app.db.base import Base
from app.models import AgentApproval, AgentRun, AgentThread

PRODUCT_TABLES = {
    "users",
    "projects",
    "tasks",
    "agent_threads",
    "agent_runs",
    "agent_approvals",
    "agent_tool_executions",
}


def _constraint_names(table: Table) -> set[str]:
    return {str(constraint.name) for constraint in table.constraints}


def test_product_agent_records_register_without_checkpoint_tables() -> None:
    assert set(Base.metadata.tables) == PRODUCT_TABLES
    assert all("checkpoint" not in name for name in Base.metadata.tables)
    assert set(AgentThread.__table__.columns.keys()) == {
        "id",
        "user_id",
        "goal_summary",
        "status",
        "created_at",
        "updated_at",
    }
    assert set(AgentRun.__table__.columns.keys()) == {
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
    assert set(AgentApproval.__table__.columns.keys()) == {
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


def test_common_uuid_and_timestamp_contract() -> None:
    for mapped in (AgentThread, AgentRun, AgentApproval):
        table = cast(Table, mapped.__table__)
        assert isinstance(table.c.id.type, postgresql.UUID)
        assert table.c.id.primary_key is True
        assert str(cast(DefaultClause, table.c.id.server_default).arg) == (
            "gen_random_uuid()"
        )
        for name in ("created_at", "updated_at"):
            assert isinstance(table.c[name].type, DateTime)
            assert cast(DateTime, table.c[name].type).timezone is True
            assert str(cast(DefaultClause, table.c[name].server_default).arg) == (
                "CURRENT_TIMESTAMP"
            )


def test_thread_columns_constraints_and_indexes_are_exact() -> None:
    table = cast(Table, AgentThread.__table__)
    assert isinstance(table.c.goal_summary.type, String)
    assert table.c.goal_summary.type.length == 2000
    assert table.c.goal_summary.nullable is False
    assert cast(String, table.c.status.type).length == 20
    assert str(cast(DefaultClause, table.c.status.server_default).arg) == "'ACTIVE'"
    assert _constraint_names(table) == {
        "pk_agent_threads",
        "uq_agent_threads_id_user_id",
        "fk_agent_threads_user_id_users",
        "ck_agent_threads_goal_summary_not_blank",
        "ck_agent_threads_status",
    }
    foreign_key = next(
        item for item in table.constraints if isinstance(item, ForeignKeyConstraint)
    )
    assert tuple(item.target_fullname for item in foreign_key.elements) == ("users.id",)
    assert {index.name for index in table.indexes} == {
        "ix_agent_threads_user_id",
        "ix_agent_threads_user_status",
    }


def test_run_columns_metrics_constraints_and_owner_foreign_key_are_exact() -> None:
    table = cast(Table, AgentRun.__table__)
    assert cast(String, table.c.status.type).length == 32
    assert cast(String, table.c.current_node.type).length == 64
    assert cast(String, table.c.summary.type).length == 2000
    assert cast(String, table.c.error_code.type).length == 64
    assert cast(String, table.c.prompt_version.type).length == 64
    for name in (
        "model_round_count",
        "provider_attempt_count",
        "tool_call_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    ):
        assert isinstance(table.c[name].type, Integer)
        assert str(cast(DefaultClause, table.c[name].server_default).arg) == "0"
    assert isinstance(table.c.latency_ms.type, Float)
    assert _constraint_names(table) == {
        "pk_agent_runs",
        "uq_agent_runs_id_user_id",
        "fk_agent_runs_thread_id_user_id_agent_threads",
        "ck_agent_runs_status",
        "ck_agent_runs_current_node",
        "ck_agent_runs_error_code",
        "ck_agent_runs_metrics_nonnegative",
        "ck_agent_runs_total_tokens",
    }
    foreign_key = next(
        item for item in table.constraints if isinstance(item, ForeignKeyConstraint)
    )
    assert tuple(item.target_fullname for item in foreign_key.elements) == (
        "agent_threads.id",
        "agent_threads.user_id",
    )
    assert {index.name for index in table.indexes} == {
        "ix_agent_runs_thread_id",
        "ix_agent_runs_user_id",
        "ix_agent_runs_user_status",
    }


def test_approval_constraints_bind_owner_revision_fingerprint_and_decision() -> None:
    table = cast(Table, AgentApproval.__table__)
    assert table.c.revision.nullable is False
    assert cast(String, table.c.proposal_fingerprint.type).length == 64
    assert cast(String, table.c.feedback.type).length == 1000
    assert isinstance(table.c.decided_at.type, DateTime)
    assert table.c.decided_at.type.timezone is True
    assert _constraint_names(table) == {
        "pk_agent_approvals",
        "fk_agent_approvals_run_id_user_id_agent_runs",
        "uq_agent_approvals_run_id_revision",
        "ck_agent_approvals_revision",
        "ck_agent_approvals_proposal_fingerprint",
        "ck_agent_approvals_decision",
        "ck_agent_approvals_decision_state",
    }
    unique = next(
        item for item in table.constraints if isinstance(item, UniqueConstraint)
    )
    assert tuple(column.name for column in unique.columns) == ("run_id", "revision")
    checks = {
        item.name: str(item.sqltext)
        for item in table.constraints
        if isinstance(item, CheckConstraint)
    }
    assert "BETWEEN 0 AND 2" in checks["ck_agent_approvals_revision"]
    assert "{64}" in checks["ck_agent_approvals_proposal_fingerprint"]
    assert "REQUEST_CHANGES" in checks["ck_agent_approvals_decision_state"]
    assert {index.name for index in table.indexes} == {
        "ix_agent_approvals_run_id",
        "ix_agent_approvals_user_id",
    }
