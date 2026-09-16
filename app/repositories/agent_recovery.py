"""PostgreSQL transaction lock for one durable Agent recovery operation."""

from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import blake2b
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

AGENT_RECOVERY_LOCK_MESSAGE = "Agent recovery lock is unavailable"
_LOCK_PERSONALIZATION = b"stms-r5"


class AgentRecoveryLockError(RuntimeError):
    """Hide database and lock diagnostics from the workflow boundary."""


def agent_recovery_lock_key(run_id: UUID) -> int:
    """Map a complete UUID deterministically to PostgreSQL's signed bigint key."""

    if not isinstance(run_id, UUID):
        raise TypeError("Agent recovery run ID must be a UUID")
    digest = blake2b(
        run_id.bytes,
        digest_size=8,
        person=_LOCK_PERSONALIZATION,
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


def _engine_for(session: Session) -> Engine:
    bind = session.get_bind()
    return bind.engine if isinstance(bind, Connection) else bind


@contextmanager
def open_agent_recovery_lock(session: Session, run_id: UUID) -> Iterator[None]:
    """Hold one independent transaction-scoped lock through reconciliation.

    Closing the dedicated connection, including after process failure, releases
    the transaction advisory lock before that connection can return to the pool.
    Hash collisions only serialize unrelated runs; they cannot permit overlap.
    """

    try:
        engine = _engine_for(session)
    except Exception:
        raise AgentRecoveryLockError(AGENT_RECOVERY_LOCK_MESSAGE) from None
    if engine.dialect.name != "postgresql":
        raise AgentRecoveryLockError(AGENT_RECOVERY_LOCK_MESSAGE)
    with engine.connect() as connection, connection.begin():
        try:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": agent_recovery_lock_key(run_id)},
            )
        except Exception:
            raise AgentRecoveryLockError(AGENT_RECOVERY_LOCK_MESSAGE) from None
        yield
