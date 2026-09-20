"""The refresh repository never receives raw tokens or owns a transaction."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.orm import Session

from app.repositories.refresh_tokens import RefreshTokenRepository


def test_insert_passes_only_digest_owner_and_times_to_orm() -> None:
    session = MagicMock(spec=Session)
    now = datetime(2026, 9, 17, tzinfo=UTC)
    owner = uuid4()
    record = RefreshTokenRepository(session).create(
        user_id=owner,
        token_hash="a" * 64,
        created_at=now,
        expires_at=now + timedelta(days=7),
    )
    assert record.user_id == owner
    assert record.token_hash == "a" * 64
    assert record.created_at == now
    assert record.expires_at == now + timedelta(days=7)
    assert record.revoked_at is None
    assert not hasattr(record, "token")
    session.add.assert_called_once_with(record)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
