"""Real PostgreSQL proof for the official synchronous LangGraph checkpointer."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    CheckpointMetadata,
    empty_checkpoint,
)
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import URL, Engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.agent.checkpointing import CheckpointSaver, open_postgres_checkpointer
from app.models import AgentApproval, AgentRun, AgentThread

pytestmark = pytest.mark.integration


def _business_record_counts(engine: Engine) -> tuple[int, int, int]:
    with engine.connect() as connection:
        return (
            connection.scalar(select(func.count()).select_from(AgentThread)) or 0,
            connection.scalar(select(func.count()).select_from(AgentRun)) or 0,
            connection.scalar(select(func.count()).select_from(AgentApproval)) or 0,
        )


def test_official_postgres_checkpointer_writes_and_recovers_synthetic_state(
    test_database_url: URL,
    integration_engine: Engine,
) -> None:
    """Use only public Saver APIs and keep product audit records independent."""

    schema_name = f"checkpoint_test_{uuid4().hex}"
    thread_id = str(uuid4())
    checkpoint_namespace = f"synthetic-{uuid4().hex}"
    with integration_engine.begin() as connection:
        connection.execute(CreateSchema(schema_name))

    checkpoint_url = test_database_url.update_query_dict(
        {"options": f"-csearch_path={schema_name},public"}
    )
    database_secret = SecretStr(checkpoint_url.render_as_string(hide_password=False))
    before_counts = _business_record_counts(integration_engine)
    saver: CheckpointSaver | None = None
    try:
        with open_postgres_checkpointer(
            database_url=database_secret,
            setup=True,
        ) as opened_saver:
            saver = opened_saver
            config: RunnableConfig = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_namespace,
                }
            }
            checkpoint = empty_checkpoint()
            checkpoint["id"] = str(uuid4())
            checkpoint["ts"] = datetime.now(UTC).isoformat()
            checkpoint["channel_values"] = {
                "synthetic_state": {"status": "ready", "step": 1}
            }
            checkpoint["channel_versions"] = {"synthetic_state": "1"}
            metadata: CheckpointMetadata = {
                "source": "input",
                "step": 0,
                "parents": {},
            }

            saved_config = opened_saver.put(
                config,
                checkpoint,
                metadata,
                {"synthetic_state": "1"},
            )
            recovered = opened_saver.get_tuple(saved_config)

            assert recovered is not None
            assert recovered.config["configurable"]["thread_id"] == thread_id
            assert recovered.checkpoint["channel_values"]["synthetic_state"] == {
                "status": "ready",
                "step": 1,
            }

        assert saver is not None
        assert _business_record_counts(integration_engine) == before_counts
    finally:
        with integration_engine.begin() as connection:
            connection.execute(DropSchema(schema_name, cascade=True))
