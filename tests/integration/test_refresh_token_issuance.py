"""Real commit/rollback and hash-only insertion on the dedicated test database."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, event, func, insert, select
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session

from app.core.exceptions import (
    REFRESH_TOKEN_ISSUANCE_MESSAGE,
    RefreshTokenIssuanceError,
)
from app.core.refresh_tokens import hash_refresh_token
from app.models import RefreshToken, User
from app.services.refresh_tokens import issue_refresh_token

pytestmark = pytest.mark.integration
ISSUED_AT = datetime(2026, 9, 17, tzinfo=UTC)


@pytest.fixture
def owners(
    integration_engine: Engine, migration_test_database_url: URL
) -> Iterator[tuple[UUID, UUID]]:
    assert migration_test_database_url is not None
    identifiers = (uuid4(), uuid4())
    with integration_engine.begin() as connection:
        for identifier in identifiers:
            connection.execute(
                insert(User).values(
                    id=identifier,
                    email=f"refresh-issuance-{identifier}@example.test",
                    password_hash="test-only-not-a-password-hash",
                )
            )
    try:
        yield identifiers
    finally:
        with integration_engine.begin() as connection:
            connection.execute(
                delete(RefreshToken).where(RefreshToken.user_id.in_(identifiers))
            )
            connection.execute(delete(User).where(User.id.in_(identifiers)))


def count_owned(engine: Engine, owner: UUID) -> int:
    with Session(engine) as session:
        count = session.scalar(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.user_id == owner)
        )
        assert count is not None
        return count


def test_committed_records_are_hash_only_and_keep_authenticated_owner(
    integration_engine: Engine,
    owners: tuple[UUID, UUID],
    caplog: pytest.LogCaptureFixture,
) -> None:
    first_token = SecretStr("a" * 43)
    second_token = SecretStr("b" * 43)
    raw_seen: list[bool] = []

    def observe_sql(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        serialized = statement + repr(parameters)
        raw_seen.append(
            any(
                token.get_secret_value() in serialized
                for token in (first_token, second_token)
            )
        )

    event.listen(integration_engine, "before_cursor_execute", observe_sql)
    try:
        for owner, token in zip(owners, (first_token, second_token), strict=True):
            with Session(integration_engine) as session:
                result = issue_refresh_token(
                    owner,
                    session,
                    issued_at=ISSUED_AT,
                    token_generator=iter((token,)).__next__,
                )
                assert not session.in_transaction()
            # Independent session proves the result was committed, not merely flushed.
            with Session(integration_engine) as reader:
                records = list(
                    reader.scalars(
                        select(RefreshToken).where(RefreshToken.user_id == owner)
                    )
                )
                assert len(records) == 1
                record = records[0]
                digest_matches = record.token_hash == hash_refresh_token(token)
                assert digest_matches
                assert record.created_at == ISSUED_AT
                assert record.expires_at == ISSUED_AT + timedelta(days=7)
                assert record.expires_at == result.expires_at
                assert record.revoked_at is None
                assert result.token == token
                assert token.get_secret_value() not in repr(record)
                assert token.get_secret_value() not in repr(result)
        assert raw_seen and not any(raw_seen)
        assert first_token.get_secret_value() not in caplog.text
        assert second_token.get_secret_value() not in caplog.text
        assert hash_refresh_token(first_token) not in caplog.text
    finally:
        event.remove(integration_engine, "before_cursor_execute", observe_sql)


def test_digest_collision_rolls_back_and_does_not_overwrite_another_owner(
    integration_engine: Engine,
    owners: tuple[UUID, UUID],
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = SecretStr("c" * 43)
    with Session(integration_engine) as session:
        issue_refresh_token(owners[0], session, token_generator=lambda: token)
    with Session(integration_engine) as session:
        with pytest.raises(RefreshTokenIssuanceError) as error:
            issue_refresh_token(owners[1], session, token_generator=lambda: token)
        assert str(error.value) == REFRESH_TOKEN_ISSUANCE_MESSAGE
        assert not session.in_transaction()
    assert count_owned(integration_engine, owners[0]) == 1
    assert count_owned(integration_engine, owners[1]) == 0
    with Session(integration_engine) as session:
        issue_refresh_token(owners[1], session)
    assert count_owned(integration_engine, owners[1]) == 1
    assert token.get_secret_value() not in caplog.text
    assert hash_refresh_token(token) not in caplog.text


def test_failure_after_flush_before_commit_leaves_no_credential(
    integration_engine: Engine, owners: tuple[UUID, UUID]
) -> None:
    class FailingCommitSession(Session):
        def commit(self) -> None:
            raise RuntimeError("test-only forced commit failure")

    with FailingCommitSession(integration_engine) as session:
        with pytest.raises(RefreshTokenIssuanceError) as error:
            issue_refresh_token(owners[0], session)
        assert str(error.value) == REFRESH_TOKEN_ISSUANCE_MESSAGE
        assert not session.in_transaction()
    assert count_owned(integration_engine, owners[0]) == 0
    with Session(integration_engine) as retry_session:
        issue_refresh_token(owners[0], retry_session)
    assert count_owned(integration_engine, owners[0]) == 1


def test_nonexistent_identity_cannot_persist_a_credential(
    integration_engine: Engine, owners: tuple[UUID, UUID]
) -> None:
    nonexistent = uuid4()
    with Session(integration_engine) as session:
        with pytest.raises(RefreshTokenIssuanceError) as error:
            issue_refresh_token(nonexistent, session)
        assert str(error.value) == REFRESH_TOKEN_ISSUANCE_MESSAGE
        assert not session.in_transaction()
    assert count_owned(integration_engine, nonexistent) == 0
    assert count_owned(integration_engine, owners[0]) == 0
