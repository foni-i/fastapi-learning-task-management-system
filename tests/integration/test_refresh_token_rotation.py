"""Real atomic rotation, row-lock contention and rollback verification."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Queue
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session

from app.core.exceptions import (
    REFRESH_TOKEN_ROTATION_MESSAGE,
    InvalidRefreshTokenError,
    RefreshTokenRotationError,
)
from app.core.refresh_tokens import INVALID_REFRESH_TOKEN_MESSAGE, hash_refresh_token
from app.models import RefreshToken, User
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.services.refresh_tokens import (
    IssuedRefreshToken,
    issue_refresh_token,
    rotate_refresh_token,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 18, tzinfo=UTC)


@pytest.fixture
def rotation_owners(
    integration_engine: Engine, migration_test_database_url: URL
) -> Iterator[tuple[UUID, UUID]]:
    assert migration_test_database_url is not None
    owners = (uuid4(), uuid4())
    with integration_engine.begin() as connection:
        for owner in owners:
            connection.execute(
                insert(User).values(
                    id=owner,
                    email=f"rotation-{owner}@example.test",
                    password_hash="test-only-not-a-password-hash",
                )
            )
    try:
        yield owners
    finally:
        with integration_engine.begin() as connection:
            connection.execute(
                delete(RefreshToken).where(RefreshToken.user_id.in_(owners))
            )
            connection.execute(delete(User).where(User.id.in_(owners)))


def seed(engine: Engine, owner: UUID) -> IssuedRefreshToken:
    with Session(engine) as session:
        return issue_refresh_token(owner, session, issued_at=NOW - timedelta(hours=1))


def records(engine: Engine, owner: UUID) -> list[RefreshToken]:
    with Session(engine) as session:
        return list(
            session.scalars(select(RefreshToken).where(RefreshToken.user_id == owner))
        )


def test_rotation_rejects_replay_and_allows_only_the_replacement(
    integration_engine: Engine,
    rotation_owners: tuple[UUID, UUID],
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner, other = rotation_owners
    initial = seed(integration_engine, owner)
    foreign = seed(integration_engine, other)
    with Session(integration_engine) as session:
        rotated = rotate_refresh_token(initial.token, session, clock=lambda: NOW)
        assert not session.in_transaction()
    assert rotated.user_id == owner
    assert rotated.expires_at == NOW + timedelta(days=7)
    assert rotated.token != initial.token
    stored = records(integration_engine, owner)
    assert len(stored) == 2
    assert sum(row.revoked_at is None for row in stored) == 1
    assert any(
        row.token_hash == hash_refresh_token(initial.token) and row.revoked_at == NOW
        for row in stored
    )
    assert any(
        row.token_hash == hash_refresh_token(rotated.token) and row.revoked_at is None
        for row in stored
    )
    for _ in range(2):
        with Session(integration_engine) as session:
            with pytest.raises(InvalidRefreshTokenError) as error:
                rotate_refresh_token(initial.token, session, clock=lambda: NOW)
            assert str(error.value) == INVALID_REFRESH_TOKEN_MESSAGE
            assert not session.in_transaction()
    assert len(records(integration_engine, owner)) == 2
    assert len(records(integration_engine, other)) == 1
    assert records(integration_engine, other)[0].revoked_at is None
    with Session(integration_engine) as session:
        again = rotate_refresh_token(
            rotated.token, session, clock=lambda: NOW + timedelta(seconds=1)
        )
    assert again.user_id == owner
    assert (
        sum(row.revoked_at is None for row in records(integration_engine, owner)) == 1
    )
    for token in (initial.token, rotated.token, foreign.token):
        assert token.get_secret_value() not in caplog.text
        assert hash_refresh_token(token) not in caplog.text
        assert token.get_secret_value() not in repr(again)


@pytest.mark.parametrize(
    "state", ["malformed", "unknown", "expired", "at-expiry", "revoked"]
)
def test_invalid_credentials_do_not_insert_or_change_other_rows(
    integration_engine: Engine, rotation_owners: tuple[UUID, UUID], state: str
) -> None:
    owner = rotation_owners[0]
    initial = seed(integration_engine, owner)
    token = initial.token
    if state in ("expired", "at-expiry", "revoked"):
        changes = (
            {"revoked_at": NOW}
            if state == "revoked"
            else {
                "expires_at": NOW - timedelta(seconds=1) if state == "expired" else NOW
            }
        )
        with integration_engine.begin() as connection:
            connection.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == owner)
                .values(**changes)
            )
    elif state == "malformed":
        token = SecretStr("invalid")
    elif state == "unknown":
        token = SecretStr("z" * 43)
    before = [
        (r.id, r.revoked_at, r.expires_at) for r in records(integration_engine, owner)
    ]
    with Session(integration_engine) as session:
        with pytest.raises(InvalidRefreshTokenError) as error:
            rotate_refresh_token(token, session, clock=lambda: NOW)
        assert str(error.value) == INVALID_REFRESH_TOKEN_MESSAGE
        assert not session.in_transaction()
    assert [
        (r.id, r.revoked_at, r.expires_at) for r in records(integration_engine, owner)
    ] == before


@pytest.mark.parametrize("stage", ["entropy", "collision", "commit"])
def test_failure_restores_old_credential_and_allows_later_rotation(
    integration_engine: Engine, rotation_owners: tuple[UUID, UUID], stage: str
) -> None:
    owner = rotation_owners[0]
    initial = seed(integration_engine, owner)

    class FailingCommitSession(Session):
        def commit(self) -> None:
            raise RuntimeError("test-only pre-commit failure")

    def replacement() -> SecretStr:
        if stage == "entropy":
            raise RuntimeError("test-only entropy failure after revocation")
        # Old digest uniqueness forces INSERT failure after the revocation UPDATE.
        return initial.token if stage == "collision" else SecretStr("x" * 43)

    session_class = FailingCommitSession if stage == "commit" else Session
    with session_class(integration_engine) as session:
        with pytest.raises(RefreshTokenRotationError) as error:
            rotate_refresh_token(
                initial.token, session, clock=lambda: NOW, token_generator=replacement
            )
        assert str(error.value) == REFRESH_TOKEN_ROTATION_MESSAGE
        assert not session.in_transaction()
    saved = records(integration_engine, owner)
    assert len(saved) == 1 and saved[0].revoked_at is None
    with Session(integration_engine) as retry_session:
        result = rotate_refresh_token(initial.token, retry_session, clock=lambda: NOW)
    assert result.user_id == owner
    assert len(records(integration_engine, owner)) == 2


def test_stale_session_cannot_reuse_a_concurrently_revoked_token(
    integration_engine: Engine, rotation_owners: tuple[UUID, UUID]
) -> None:
    owner = rotation_owners[0]
    initial = seed(integration_engine, owner)
    with Session(integration_engine) as stale:
        cached = stale.scalar(select(RefreshToken).where(RefreshToken.user_id == owner))
        assert cached is not None and cached.revoked_at is None
        with Session(integration_engine) as winner:
            rotate_refresh_token(initial.token, winner, clock=lambda: NOW)
        assert cached.revoked_at is None
        with pytest.raises(InvalidRefreshTokenError):
            rotate_refresh_token(initial.token, stale, clock=lambda: NOW)
    assert len(records(integration_engine, owner)) == 2


def test_two_sessions_contend_on_the_row_and_only_one_rotates(
    integration_engine: Engine, rotation_owners: tuple[UUID, UUID]
) -> None:
    initial = seed(integration_engine, rotation_owners[0])
    locked, release = Event(), Event()
    pids: Queue[int] = Queue()

    class HoldingRepository(RefreshTokenRepository):
        def get_by_hash_for_update(self, token_hash: str) -> RefreshToken | None:
            record = super().get_by_hash_for_update(token_hash)
            locked.set()
            if not release.wait(timeout=8):
                raise RuntimeError("test synchronization timed out")
            return record

    def rotate(hold: bool) -> str:
        with Session(integration_engine) as session:
            pid = session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            pids.put(pid)
            try:
                rotate_refresh_token(
                    initial.token,
                    session,
                    clock=lambda: NOW,
                    repository_factory=HoldingRepository
                    if hold
                    else RefreshTokenRepository,
                )
                return "success"
            except InvalidRefreshTokenError:
                return "invalid"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(rotate, True)
        try:
            first_pid = pids.get(timeout=3)
            assert locked.wait(timeout=3)
            second = executor.submit(rotate, False)
            second_pid = pids.get(timeout=3)
            deadline = monotonic() + 3
            blocked = False
            while monotonic() < deadline:
                with integration_engine.connect() as observer:
                    blockers = observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid}
                    )
                if first_pid in blockers:
                    blocked = True
                    break
                sleep(0.01)
            assert blocked, "Expected a real PostgreSQL row-lock wait"
        finally:
            release.set()
        assert first.result(timeout=10) == "success"
        assert second.result(timeout=10) == "invalid"
    stored = records(integration_engine, rotation_owners[0])
    assert len(stored) == 2
    assert sum(row.revoked_at is None for row in stored) == 1
