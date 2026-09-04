"""Official synchronous PostgreSQL checkpoint lifecycle for LangGraph."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from uuid import UUID

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.core.config import POSTGRESQL_DRIVER, get_settings

CHECKPOINT_CONFIGURATION_MESSAGE = "Agent checkpoint database is not configured"
CHECKPOINT_UNAVAILABLE_MESSAGE = "Agent checkpoint storage is unavailable"

type CheckpointSaver = PostgresSaver
type SaverContextFactory = Callable[[str], AbstractContextManager[CheckpointSaver]]
type LangGraphThreadConfig = dict[str, dict[str, str]]


class AgentCheckpointConfigurationError(RuntimeError):
    """Reject missing or invalid checkpoint configuration without echoing it."""


class AgentCheckpointUnavailableError(RuntimeError):
    """Hide connection and setup diagnostics at the checkpoint boundary."""


def build_thread_checkpoint_config(thread_id: UUID) -> LangGraphThreadConfig:
    """Map one trusted product thread UUID to exact LangGraph configuration."""

    if not isinstance(thread_id, UUID):
        raise TypeError("Agent checkpoint thread ID must be a UUID")
    return {"configurable": {"thread_id": str(thread_id)}}


def _to_psycopg_conninfo(database_url: SecretStr) -> str:
    """Convert the validated SQLAlchemy URL to Psycopg's URI dialect."""

    raw_url = database_url.get_secret_value()
    try:
        parsed = make_url(raw_url)
    except ArgumentError, ValueError:
        raise AgentCheckpointConfigurationError(
            CHECKPOINT_CONFIGURATION_MESSAGE
        ) from None
    if parsed.drivername != POSTGRESQL_DRIVER or not parsed.host or not parsed.database:
        raise AgentCheckpointConfigurationError(CHECKPOINT_CONFIGURATION_MESSAGE)
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


def create_postgres_saver(
    connection: Connection[DictRow],
) -> PostgresSaver:
    """Create the official saver with strict schema-derived deserialization."""

    serializer = JsonPlusSerializer(allowed_msgpack_modules=None)
    return PostgresSaver(connection, serde=serializer)


@contextmanager
def _official_saver_context(conninfo: str) -> Iterator[CheckpointSaver]:
    """Own one synchronous Psycopg connection for one saver lifetime."""

    with Connection.connect(
        conninfo,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    ) as connection:
        yield create_postgres_saver(connection)


@contextmanager
def open_postgres_checkpointer(
    *,
    database_url: SecretStr | None = None,
    setup: bool = False,
    saver_factory: SaverContextFactory = _official_saver_context,
) -> Iterator[CheckpointSaver]:
    """Open and always close one official saver without exposing credentials.

    Product repositories must never query the library-owned checkpoint tables.
    The optional factory exists only to keep ordinary tests connection-free.
    """

    configured_url = database_url or get_settings().database_url
    if configured_url is None:
        raise AgentCheckpointConfigurationError(CHECKPOINT_CONFIGURATION_MESSAGE)
    conninfo = _to_psycopg_conninfo(configured_url)

    stack = ExitStack()
    try:
        saver = stack.enter_context(saver_factory(conninfo))
        if setup:
            saver.setup()
    except Exception as exc:
        stack.close()
        if isinstance(exc, AgentCheckpointConfigurationError):
            raise
        raise AgentCheckpointUnavailableError(CHECKPOINT_UNAVAILABLE_MESSAGE) from None

    try:
        yield saver
    finally:
        try:
            stack.close()
        except Exception:
            raise AgentCheckpointUnavailableError(
                CHECKPOINT_UNAVAILABLE_MESSAGE
            ) from None
