"""Stage 5 acceptance: lifecycle, real lock timeouts and post-commit ambiguity."""

from collections.abc import Iterator
from time import monotonic

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session

from app.core.exceptions import AUTHENTICATION_UNAVAILABLE_MESSAGE
from app.core.refresh_tokens import hash_refresh_token
from app.models import RefreshToken, User
from app.repositories.users import UserRepository
from tests.integration.test_refresh_http import (
    CHANGE_PASSWORD,
    LOGIN,
    LOGOUT,
    NEW_PASSWORD,
    PASSWORD,
    REFRESH,
    Harness,
)

pytestmark = pytest.mark.integration
PATHS = {
    "login": LOGIN,
    "refresh": REFRESH,
    "logout": LOGOUT,
    "change": CHANGE_PASSWORD,
}


@pytest.fixture
def security_harness(
    integration_engine: Engine,
    migration_test_database_url: URL,
) -> Iterator[Harness]:
    assert migration_test_database_url is not None
    instance = Harness(integration_engine)
    try:
        yield instance
    finally:
        # Only synthetic owners created by this fixture are removed.
        with integration_engine.begin() as connection:
            owners = select(User.id).where(User.email.in_(instance.emails))
            connection.execute(
                delete(RefreshToken).where(RefreshToken.user_id.in_(owners))
            )
            connection.execute(delete(User).where(User.email.in_(instance.emails)))


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    response = client.post(LOGIN, json={"email": email, "password": password})
    assert response.status_code == 200
    result: dict[str, str] = response.json()
    return result


def request(
    client: TestClient, kind: str, email: str, pair: dict[str, str]
) -> Response:
    if kind == "login":
        body = {"email": email, "password": PASSWORD}
    elif kind == "change":
        body = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    else:
        body = {"refresh_token": pair["refresh_token"]}
    return client.post(
        PATHS[kind],
        json=body,
        headers={"Authorization": f"Bearer {pair['access_token']}"},
    )


def assert_unavailable(response: Response) -> None:
    assert response.status_code == 503
    assert response.json() == {"detail": AUTHENTICATION_UNAVAILABLE_MESSAGE}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert "set-cookie" not in response.headers


def test_two_owner_complete_credential_lifecycle(
    security_harness: Harness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = security_harness
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        other_email, other = harness.register(client)
        first, second = login(client, email), login(client, email)
        foreign = login(client, other_email)
        rotated = request(client, "refresh", email, first)
        assert rotated.status_code == 200
        replacement: dict[str, str] = rotated.json()
        assert request(client, "refresh", email, first).status_code == 401
        assert request(client, "logout", email, replacement).status_code == 204
        assert request(client, "logout", email, replacement).status_code == 204
        assert request(client, "refresh", email, replacement).status_code == 401
        # The other login survives a presented-token logout, but not password change.
        second_rotation = request(client, "refresh", email, second)
        assert second_rotation.status_code == 200
        surviving: dict[str, str] = second_rotation.json()
        before_change = harness.rows(owner)
        for pair in (first, second, replacement, surviving):
            digest = hash_refresh_token(SecretStr(pair["refresh_token"]))
            assert any(row.token_hash == digest for row in before_change)
        assert request(client, "change", email, first).status_code == 204
        assert all(row.revoked_at is not None for row in harness.rows(owner))
        assert harness.rows(other)[0].revoked_at is None
        for pair in (first, second, replacement, surviving):
            assert request(client, "refresh", email, pair).status_code == 401
        assert (
            client.post(LOGIN, json={"email": email, "password": PASSWORD}).status_code
            == 401
        )
        fresh = login(client, email, NEW_PASSWORD)
        assert request(client, "refresh", email, fresh).status_code == 200
        assert request(client, "refresh", other_email, foreign).status_code == 200
        for pair, expected in ((first, owner), (fresh, owner), (foreign, other)):
            me = client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {pair['access_token']}"},
            )
            assert me.status_code == 200 and me.json()["id"] == str(expected)
        for pair in (first, second, replacement, surviving, foreign, fresh):
            assert pair["refresh_token"] not in caplog.text
            assert pair["access_token"] not in caplog.text
        assert PASSWORD not in caplog.text and NEW_PASSWORD not in caplog.text
        assert all(row.token_hash not in caplog.text for row in before_change)


@pytest.mark.parametrize("kind", list(PATHS))
def test_real_user_lock_timeout_is_safe_and_retryable(
    security_harness: Harness,
    kind: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = security_harness
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        pair = login(client, email)
        with Session(harness.engine) as blocker:
            assert UserRepository(blocker).get_by_id_for_update(owner) is not None
            started = monotonic()
            response = request(client, kind, email, pair)
            elapsed = monotonic() - started
            assert_unavailable(response)
            # Exercise production's five-second DB lock_timeout, not a mock failure.
            assert elapsed >= 4
        rows = harness.rows(owner)
        assert len(rows) == 1 and rows[0].revoked_at is None
        retry = request(client, kind, email, pair)
        assert retry.status_code == (200 if kind in {"login", "refresh"} else 204)
        assert pair["refresh_token"] not in caplog.text
        assert rows[0].token_hash not in caplog.text
        assert "lock timeout" not in caplog.text


@pytest.mark.parametrize("kind", list(PATHS))
def test_committed_write_cannot_be_undone_by_lost_acknowledgement(
    security_harness: Harness,
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Simulate an exception AFTER real commit, not an actual network outage."""
    harness = security_harness
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        pair = login(client, email)
        original_commit = Session.commit
        with monkeypatch.context() as patch:

            def commit_then_fail(self: Session) -> None:
                original_commit(self)
                raise RuntimeError(pair["refresh_token"])

            patch.setattr(Session, "commit", commit_then_fail)
            response = request(client, kind, email, pair)
        assert_unavailable(response)
        rows = harness.rows(owner)
        if kind == "login":
            # The undelivered credential exists; retry is a new login, not deduplication.
            assert len(rows) == 2 and all(row.revoked_at is None for row in rows)
            login(client, email)
            assert len(harness.rows(owner)) == 3
        elif kind == "refresh":
            assert len(rows) == 2 and sum(row.revoked_at is None for row in rows) == 1
            assert request(client, "refresh", email, pair).status_code == 401
            login(client, email)
        elif kind == "logout":
            assert len(rows) == 1 and rows[0].revoked_at is not None
            revoked_at = rows[0].revoked_at
            assert request(client, "logout", email, pair).status_code == 204
            assert harness.rows(owner)[0].revoked_at == revoked_at
        else:
            assert len(rows) == 1 and rows[0].revoked_at is not None
            assert request(client, "change", email, pair).status_code == 401
            assert (
                client.post(
                    LOGIN, json={"email": email, "password": PASSWORD}
                ).status_code
                == 401
            )
            login(client, email, NEW_PASSWORD)
        assert pair["refresh_token"] not in caplog.text
        assert all(row.token_hash not in caplog.text for row in rows)


@pytest.mark.parametrize("kind", list(PATHS))
def test_real_http_validation_never_reflects_secrets_or_mutates(
    security_harness: Harness,
    kind: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = security_harness
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        pair = login(client, email)
        token = pair["refresh_token"]
        headers = {
            "Authorization": f"Bearer {pair['access_token']}",
            "Content-Type": "application/json",
        }
        bodies = (
            '{"' + token + '":',
            '{"' + token + '": "' + PASSWORD + '"}',
            b"\xff\xfe\xff",
        )
        for body in bodies:
            response = client.post(PATHS[kind], content=body, headers=headers)
            assert response.status_code == 422
            assert token not in response.text and PASSWORD not in response.text
            assert "input" not in response.text and "ctx" not in response.text
            assert len(response.json()["detail"]) <= 3
            assert response.headers["cache-control"] == "no-store"
        rows = harness.rows(owner)
        assert len(rows) == 1 and rows[0].revoked_at is None
        assert token not in caplog.text and PASSWORD not in caplog.text
