"""Connection-free checks for Task 10.5 storage and public boundaries."""

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, Table
from sqlalchemy.schema import ForeignKeyConstraint, UniqueConstraint

from app.models import AgentToolExecution
from app.schemas.agent_tool_execution import PublicAgentToolExecution


def test_execution_storage_has_exact_safe_columns_and_identity_constraint() -> None:
    table = cast(Table, AgentToolExecution.__table__)
    assert set(table.columns.keys()) == {
        "id",
        "run_id",
        "user_id",
        "revision",
        "proposal_fingerprint",
        "action_key",
        "tool_name",
        "status",
        "attempt_count",
        "result_task_id",
        "result_summary",
        "error_code",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    }
    unique = next(
        item for item in table.constraints if isinstance(item, UniqueConstraint)
    )
    assert unique.name == "uq_agent_tool_executions_action_identity"
    assert tuple(column.name for column in unique.columns) == (
        "run_id",
        "revision",
        "proposal_fingerprint",
        "action_key",
    )
    foreign_key = next(
        item for item in table.constraints if isinstance(item, ForeignKeyConstraint)
    )
    assert foreign_key.name == ("fk_agent_tool_executions_run_id_user_id_agent_runs")
    assert tuple(item.target_fullname for item in foreign_key.elements) == (
        "agent_runs.id",
        "agent_runs.user_id",
    )
    checks = {
        item.name: str(item.sqltext)
        for item in table.constraints
        if isinstance(item, CheckConstraint)
    }
    assert "UNKNOWN" in checks["ck_agent_tool_executions_status"]
    assert "result_task_id" in checks["ck_agent_tool_executions_state"]
    tool_names = checks["ck_agent_tool_executions_tool_name"]
    for name in (
        "create_task",
        "update_task",
        "batch_create_tasks",
        "delete_task",
    ):
        assert name in tool_names
    for name in ("started_at", "completed_at", "created_at", "updated_at"):
        assert isinstance(table.c[name].type, DateTime)
        assert cast(DateTime, table.c[name].type).timezone is True


def test_public_execution_schema_is_a_safe_explicit_whitelist() -> None:
    now = datetime.now(UTC)
    source = AgentToolExecution(
        id=uuid4(),
        run_id=uuid4(),
        user_id=uuid4(),
        revision=1,
        proposal_fingerprint="a" * 64,
        action_key="create-first",
        tool_name="create_task",
        status="COMPLETED",
        attempt_count=1,
        result_task_id=uuid4(),
        result_summary="Safe title",
        started_at=now,
        completed_at=now,
        created_at=now,
        updated_at=now,
    )
    public = PublicAgentToolExecution.model_validate(source)
    assert set(public.model_dump()) == {
        "id",
        "run_id",
        "revision",
        "action_key",
        "tool_name",
        "status",
        "attempt_count",
        "result_task_id",
        "result_summary",
        "error_code",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    }
    serialized = public.model_dump_json()
    assert "user_id" not in serialized
    assert "proposal_fingerprint" not in serialized
    assert "arguments" not in serialized
