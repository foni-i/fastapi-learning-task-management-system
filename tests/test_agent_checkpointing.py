"""Connection-free contracts for the official PostgreSQL checkpointer adapter."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import Connection
from psycopg.rows import DictRow
from pydantic import SecretStr

from app.agent.checkpointing import (
    CHECKPOINT_CONFIGURATION_MESSAGE,
    CHECKPOINT_UNAVAILABLE_MESSAGE,
    AgentCheckpointConfigurationError,
    AgentCheckpointUnavailableError,
    CheckpointSaver,
    create_postgres_saver,
    open_postgres_checkpointer,
)
from app.agent.state import AgentGraphState
from app.core.config import get_settings

DATABASE_PASSWORD = "synthetic-checkpoint-password"
DATABASE_URL = (
    "postgresql+psycopg://checkpoint_test:"
    f"{DATABASE_PASSWORD}@127.0.0.1:5433/checkpoint_test"
)


class FakeSaver(BaseCheckpointSaver[str]):
    def __init__(self, *, setup_error: Exception | None = None) -> None:
        super().__init__()
        self.setup_error = setup_error
        self.setup_calls = 0

    def setup(self) -> None:
        self.setup_calls += 1
        if self.setup_error is not None:
            raise self.setup_error


class SaverHarness:
    def __init__(self, saver: FakeSaver) -> None:
        self.saver = saver
        self.conninfo: str | None = None
        self.entered = 0
        self.closed = 0

    @contextmanager
    def factory(self, conninfo: str) -> Iterator[CheckpointSaver]:
        self.conninfo = conninfo
        self.entered += 1
        try:
            yield cast(CheckpointSaver, self.saver)
        finally:
            self.closed += 1


def test_missing_configuration_fails_before_constructing_saver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STMS_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    harness = SaverHarness(FakeSaver())

    with (
        pytest.raises(
            AgentCheckpointConfigurationError,
            match=CHECKPOINT_CONFIGURATION_MESSAGE,
        ),
        open_postgres_checkpointer(saver_factory=harness.factory),
    ):
        raise AssertionError("unreachable")

    assert harness.entered == 0
    assert harness.closed == 0
    get_settings.cache_clear()


def test_factory_is_lazy_runs_official_setup_and_closes() -> None:
    saver = FakeSaver()
    harness = SaverHarness(saver)
    context = open_postgres_checkpointer(
        database_url=SecretStr(DATABASE_URL),
        setup=True,
        saver_factory=harness.factory,
    )

    assert harness.entered == 0
    with context as opened:
        assert cast(object, opened) is saver
        assert harness.entered == 1
        assert saver.setup_calls == 1
        assert harness.closed == 0

    assert harness.closed == 1
    assert harness.conninfo is not None
    assert harness.conninfo.startswith("postgresql://")


def test_caller_failure_propagates_but_resource_still_closes() -> None:
    harness = SaverHarness(FakeSaver())
    caller_error = RuntimeError("synthetic caller failure")

    with (
        pytest.raises(RuntimeError) as error,
        open_postgres_checkpointer(
            database_url=SecretStr(DATABASE_URL),
            saver_factory=harness.factory,
        ),
    ):
        raise caller_error

    assert error.value is caller_error
    assert harness.closed == 1


def test_setup_failure_is_redacted_and_resource_closes() -> None:
    harness = SaverHarness(FakeSaver(setup_error=RuntimeError(DATABASE_URL)))

    with (
        pytest.raises(
            AgentCheckpointUnavailableError,
            match=CHECKPOINT_UNAVAILABLE_MESSAGE,
        ) as error,
        open_postgres_checkpointer(
            database_url=SecretStr(DATABASE_URL),
            setup=True,
            saver_factory=harness.factory,
        ),
    ):
        raise AssertionError("unreachable")

    assert DATABASE_PASSWORD not in str(error.value)
    assert DATABASE_URL not in str(error.value)
    assert harness.closed == 1


def test_invalid_url_is_rejected_without_echoing_secret() -> None:
    invalid_url = f"sqlite:///{DATABASE_PASSWORD}.db"

    with (
        pytest.raises(AgentCheckpointConfigurationError) as error,
        open_postgres_checkpointer(database_url=SecretStr(invalid_url)),
    ):
        raise AssertionError("unreachable")

    assert invalid_url not in str(error.value)
    assert DATABASE_PASSWORD not in str(error.value)


def test_create_postgres_saver_uses_official_sync_type_and_strict_serializer() -> None:
    connection = cast(Connection[DictRow], object())

    saver = create_postgres_saver(connection)

    assert type(saver) is PostgresSaver
    assert isinstance(saver.serde, JsonPlusSerializer)


def test_agent_state_contains_no_runtime_persistence_objects() -> None:
    forbidden = {"session", "connection", "checkpointer", "saver", "repository"}

    assert forbidden.isdisjoint(AgentGraphState.model_fields)
