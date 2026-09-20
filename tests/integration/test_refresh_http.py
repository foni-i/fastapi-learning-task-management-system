"""Real HTTP credential delivery, owner binding, fault recovery and contention."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Queue
from threading import Barrier, Event, Lock
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session

from app.api.v1.endpoints import auth
from app.core.exceptions import AUTHENTICATION_UNAVAILABLE_MESSAGE
from app.core.security import verify_password
from app.core.tokens import create_access_token, validate_access_token
from app.db.session import get_session
from app.main import create_app
from app.models import RefreshToken, User
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.schemas.user import PasswordChangeRequest
from app.services import authentication
from app.services.password_change import change_password

pytestmark = pytest.mark.integration
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
PASSWORD = "test-only HTTP authentication password"
LOGOUT = "/api/v1/auth/logout"
CHANGE_PASSWORD = "/api/v1/users/me/change-password"
NEW_PASSWORD = "test-only new HTTP password"


class Harness:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.app: FastAPI = create_app()
        self.emails: list[str] = []
        self.app.dependency_overrides[get_session] = self.sessions

    def sessions(self) -> Iterator[Session]:
        with Session(self.engine) as session:
            yield session

    def register(self, client: TestClient) -> tuple[str, UUID]:
        email = f"refresh-http-{uuid4()}@example.com"
        self.emails.append(email)
        response = client.post(
            "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 201
        return email, UUID(response.json()["id"])

    def rows(self, owner: UUID) -> list[RefreshToken]:
        with Session(self.engine) as session:
            return list(
                session.scalars(
                    select(RefreshToken).where(RefreshToken.user_id == owner)
                )
            )


@pytest.fixture
def harness(
    integration_engine: Engine, migration_test_database_url: URL
) -> Iterator[Harness]:
    assert migration_test_database_url is not None
    instance = Harness(integration_engine)
    try:
        yield instance
    finally:
        with integration_engine.begin() as connection:
            owners = select(User.id).where(User.email.in_(instance.emails))
            connection.execute(
                delete(RefreshToken).where(RefreshToken.user_id.in_(owners))
            )
            connection.execute(delete(User).where(User.email.in_(instance.emails)))


def test_login_refresh_current_user_and_owner_isolation(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        other_email, other = harness.register(client)
        login = client.post(LOGIN, json={"email": email, "password": PASSWORD})
        foreign = client.post(LOGIN, json={"email": other_email, "password": PASSWORD})
        assert login.status_code == foreign.status_code == 200
        pair = login.json()
        assert set(pair) == {
            "access_token",
            "token_type",
            "refresh_token",
            "refresh_expires_at",
        }
        assert len(pair["refresh_token"]) == 43
        assert validate_access_token(pair["access_token"]).subject == owner
        old_access = pair["access_token"]
        expired_access = create_access_token(
            owner, issued_at=datetime.now(UTC) - timedelta(days=1)
        )
        for bearer in (expired_access, foreign.json()["access_token"]):
            refreshed = client.post(
                REFRESH,
                json={"refresh_token": pair["refresh_token"]},
                headers={"Authorization": f"Bearer {bearer}"},
            )
            assert refreshed.status_code == 200
            assert refreshed.headers["cache-control"] == "no-store"
            assert refreshed.headers["pragma"] == "no-cache"
            assert "set-cookie" not in refreshed.headers
            replay = client.post(REFRESH, json={"refresh_token": pair["refresh_token"]})
            assert replay.status_code == 401
            pair = refreshed.json()
            assert validate_access_token(pair["access_token"]).subject == owner
            me = client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {pair['access_token']}"},
            )
            assert me.status_code == 200 and UUID(me.json()["id"]) == owner
        assert (
            client.get(
                "/api/v1/users/me", headers={"Authorization": f"Bearer {old_access}"}
            ).status_code
            == 200
        )
    rows = harness.rows(owner)
    assert len(rows) == 3 and sum(r.revoked_at is None for r in rows) == 1
    assert len(harness.rows(other)) == 1 and harness.rows(other)[0].revoked_at is None
    for value in (
        PASSWORD,
        old_access,
        pair["access_token"],
        pair["refresh_token"],
        *(r.token_hash for r in rows),
    ):
        assert value not in caplog.text


@pytest.mark.parametrize("state", ["unknown", "expired", "revoked"])
def test_stored_invalid_credentials_share_401(harness: Harness, state: str) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        login = client.post(LOGIN, json={"email": email, "password": PASSWORD})
        assert login.status_code == 200
        token = login.json()["refresh_token"]
        now = datetime.now(UTC)
        if state == "unknown":
            token = "z" * 43
        else:
            values = (
                {"revoked_at": now}
                if state == "revoked"
                else {
                    "created_at": now - timedelta(days=8),
                    "expires_at": now - timedelta(days=1),
                }
            )
            with harness.engine.begin() as connection:
                connection.execute(
                    update(RefreshToken)
                    .where(RefreshToken.user_id == owner)
                    .values(**values)
                )
        response = client.post(REFRESH, json={"refresh_token": token})
        assert response.status_code == 401
        assert response.json() == {"detail": "Refresh token is invalid"}
        assert response.headers["cache-control"] == "no-store"
    assert len(harness.rows(owner)) == 1


@pytest.mark.parametrize("kind", ["login", "refresh"])
@pytest.mark.parametrize("stage", ["sign", "insert", "commit"])
def test_http_failures_rollback_and_allow_retry(
    harness: Harness,
    kind: str,
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        credentials = {"email": email, "password": PASSWORD}
        initial = client.post(LOGIN, json=credentials)
        assert initial.status_code == 200
        token = initial.json()["refresh_token"]
        body = credentials if kind == "login" else {"refresh_token": token}
        path = LOGIN if kind == "login" else REFRESH
        with monkeypatch.context() as patch:
            if stage == "sign":

                def fail_sign(_: UUID) -> str:
                    raise RuntimeError(token)

                if kind == "login":
                    patch.setattr(
                        auth,
                        "authenticate_user",
                        lambda c, s: authentication.authenticate_user(
                            c, s, token_issuer=fail_sign
                        ),
                    )
                else:
                    patch.setattr(
                        auth,
                        "refresh_authentication",
                        lambda t, s: authentication.refresh_authentication(
                            t, s, token_issuer=fail_sign
                        ),
                    )
            elif stage == "insert":
                original = RefreshTokenRepository.create

                def fail_create(
                    self: RefreshTokenRepository,
                    *,
                    user_id: UUID,
                    token_hash: str,
                    created_at: datetime,
                    expires_at: datetime,
                ) -> RefreshToken:
                    original(
                        self,
                        user_id=user_id,
                        token_hash=token_hash,
                        created_at=created_at,
                        expires_at=expires_at,
                    )
                    raise RuntimeError(token)

                patch.setattr(RefreshTokenRepository, "create", fail_create)
            else:

                def fail_commit(self: Session) -> None:
                    raise RuntimeError(token)

                patch.setattr(Session, "commit", fail_commit)
            failed = client.post(path, json=body)
        assert failed.status_code == 503
        assert failed.json() == {"detail": AUTHENTICATION_UNAVAILABLE_MESSAGE}
        assert failed.headers["cache-control"] == "no-store"
        rows = harness.rows(owner)
        assert len(rows) == 1 and rows[0].revoked_at is None
        retry = client.post(REFRESH, json={"refresh_token": token})
        assert retry.status_code == 200
        assert len(harness.rows(owner)) == 2
        assert token not in caplog.text


def test_concurrent_http_refresh_has_one_winner(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        login = client.post(LOGIN, json={"email": email, "password": PASSWORD})
        assert login.status_code == 200
        token = login.json()["refresh_token"]
    barrier = Barrier(2, timeout=5)
    original = RefreshTokenRepository.get_by_hash_for_update

    def synchronized_lookup(
        self: RefreshTokenRepository, digest: str
    ) -> RefreshToken | None:
        barrier.wait()
        return original(self, digest)

    monkeypatch.setattr(
        RefreshTokenRepository, "get_by_hash_for_update", synchronized_lookup
    )

    def refresh() -> int:
        with TestClient(harness.app) as client:
            return client.post(REFRESH, json={"refresh_token": token}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(refresh), pool.submit(refresh)]
        assert sorted(f.result(timeout=15) for f in futures) == [200, 401]
    rows = harness.rows(owner)
    assert len(rows) == 2 and sum(r.revoked_at is None for r in rows) == 1


def test_logout_is_idempotent_and_scoped_to_presented_session(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        other_email, other = harness.register(client)
        pair = client.post(LOGIN, json={"email": email, "password": PASSWORD}).json()
        same_owner = client.post(
            LOGIN, json={"email": email, "password": PASSWORD}
        ).json()
        foreign = client.post(
            LOGIN, json={"email": other_email, "password": PASSWORD}
        ).json()
        for _ in range(2):
            response = client.post(
                LOGOUT,
                json={"refresh_token": pair["refresh_token"]},
                headers={"Authorization": f"Bearer {foreign['access_token']}"},
            )
            assert response.status_code == 204 and response.content == b""
            assert response.headers["cache-control"] == "no-store"
            assert (
                client.post(
                    REFRESH, json={"refresh_token": pair["refresh_token"]}
                ).status_code
                == 401
            )
        rows = harness.rows(owner)
        assert len(rows) == 2 and sum(r.revoked_at is not None for r in rows) == 1
        assert harness.rows(other)[0].revoked_at is None
        assert (
            client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {pair['access_token']}"},
            ).status_code
            == 200
        )
        for active in (same_owner, foreign):
            assert (
                client.post(
                    REFRESH, json={"refresh_token": active["refresh_token"]}
                ).status_code
                == 200
            )
        for value in (
            pair["refresh_token"],
            pair["access_token"],
            PASSWORD,
            *(r.token_hash for r in rows),
        ):
            assert value not in caplog.text


@pytest.mark.parametrize("state", ["unknown", "expired", "future", "revoked"])
def test_logout_state_is_not_disclosed(harness: Harness, state: str) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        pair = client.post(LOGIN, json={"email": email, "password": PASSWORD}).json()
        now = datetime.now(UTC)
        if state != "unknown":
            values = (
                {"revoked_at": now}
                if state == "revoked"
                else {
                    "created_at": now + timedelta(days=1)
                    if state == "future"
                    else now - timedelta(days=8),
                    "expires_at": now + timedelta(days=8)
                    if state == "future"
                    else now - timedelta(days=1),
                }
            )
            with harness.engine.begin() as connection:
                connection.execute(
                    update(RefreshToken)
                    .where(RefreshToken.user_id == owner)
                    .values(**values)
                )
        token = "z" * 43 if state == "unknown" else pair["refresh_token"]
        for _ in range(2):
            response = client.post(LOGOUT, json={"refresh_token": token})
            assert response.status_code == 204 and response.content == b""
        row = harness.rows(owner)[0]
        assert (row.revoked_at is None) == (state == "unknown")
        if state == "revoked":
            assert row.revoked_at == now
        if state == "future":
            assert row.revoked_at == row.created_at


@pytest.mark.parametrize("stage", ["update", "commit"])
def test_logout_failure_rolls_back_and_retry_succeeds(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        pair = client.post(LOGIN, json={"email": email, "password": PASSWORD}).json()
        token = pair["refresh_token"]
        with monkeypatch.context() as patch:
            if stage == "update":
                original = RefreshTokenRepository.revoke_if_unrevoked

                def fail_update(
                    self: RefreshTokenRepository,
                    *,
                    token_id: UUID,
                    user_id: UUID,
                    revoked_at: datetime,
                ) -> bool:
                    original(
                        self, token_id=token_id, user_id=user_id, revoked_at=revoked_at
                    )
                    raise RuntimeError(token)

                patch.setattr(
                    RefreshTokenRepository, "revoke_if_unrevoked", fail_update
                )
            else:

                def fail_commit(self: Session) -> None:
                    raise RuntimeError(token)

                patch.setattr(Session, "commit", fail_commit)
            response = client.post(LOGOUT, json={"refresh_token": token})
        assert response.status_code == 503
        assert response.json() == {"detail": AUTHENTICATION_UNAVAILABLE_MESSAGE}
        assert harness.rows(owner)[0].revoked_at is None
        assert client.post(LOGOUT, json={"refresh_token": token}).status_code == 204
        assert client.post(REFRESH, json={"refresh_token": token}).status_code == 401
        assert len(harness.rows(owner)) == 1
        assert token not in caplog.text


@pytest.mark.parametrize(
    "first_path,second_path", [(LOGOUT, REFRESH), (REFRESH, LOGOUT), (LOGOUT, LOGOUT)]
)
def test_logout_and_refresh_serialize_on_same_row(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    first_path: str,
    second_path: str,
) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        token = client.post(LOGIN, json={"email": email, "password": PASSWORD}).json()[
            "refresh_token"
        ]
    locked, release = Event(), Event()
    order_lock = Lock()
    pids: Queue[int] = Queue()
    calls = 0
    original = RefreshTokenRepository.get_by_hash_for_update

    def controlled_lookup(
        self: RefreshTokenRepository, digest: str
    ) -> RefreshToken | None:
        nonlocal calls
        with order_lock:
            calls += 1
            first = calls == 1
        pid = self._session.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        pids.put(pid)
        record = original(self, digest)
        if first:
            locked.set()
            assert release.wait(4)
        return record

    def request(path: str) -> int:
        with TestClient(harness.app) as client:
            return client.post(path, json={"refresh_token": token}).status_code

    with monkeypatch.context() as patch:
        patch.setattr(
            RefreshTokenRepository, "get_by_hash_for_update", controlled_lookup
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(request, first_path)
            try:
                assert locked.wait(3)
                first_pid = pids.get(timeout=1)
                second = pool.submit(request, second_path)
                second_pid = pids.get(timeout=1)
                deadline = monotonic() + 2
                blocked = False
                with harness.engine.connect() as observer:
                    while monotonic() < deadline:
                        blockers = observer.scalar(
                            text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid}
                        )
                        if first_pid in blockers:
                            blocked = True
                            break
                        sleep(0.02)
                assert blocked
            finally:
                release.set()
            assert first.result(timeout=10) == (200 if first_path == REFRESH else 204)
            assert second.result(timeout=10) == (401 if second_path == REFRESH else 204)
    rows = harness.rows(owner)
    assert len(rows) == (2 if first_path == REFRESH else 1)
    assert sum(row.revoked_at is None for row in rows) == (
        1 if first_path == REFRESH else 0
    )


def test_password_change_revokes_all_owned_credentials_only(
    harness: Harness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        other_email, other = harness.register(client)
        pairs = [
            client.post(LOGIN, json={"email": email, "password": PASSWORD}).json()
            for _ in range(3)
        ]
        foreign = client.post(
            LOGIN, json={"email": other_email, "password": PASSWORD}
        ).json()
        headers = {"Authorization": f"Bearer {pairs[0]['access_token']}"}
        rotated = client.post(
            REFRESH, json={"refresh_token": pairs[0]["refresh_token"]}
        ).json()
        old_revoked = next(
            r.revoked_at for r in harness.rows(owner) if r.revoked_at is not None
        )
        response = client.post(
            CHANGE_PASSWORD,
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            headers=headers,
        )
        assert response.status_code == 204 and response.content == b""
        assert response.headers["cache-control"] == "no-store"
        rows = harness.rows(owner)
        assert len(rows) == 4 and all(r.revoked_at is not None for r in rows)
        assert any(r.revoked_at == old_revoked for r in rows)
        assert harness.rows(other)[0].revoked_at is None
        for pair in (*pairs, rotated):
            assert (
                client.post(
                    REFRESH, json={"refresh_token": pair["refresh_token"]}
                ).status_code
                == 401
            )
        assert (
            client.post(LOGIN, json={"email": email, "password": PASSWORD}).status_code
            == 401
        )
        assert (
            client.post(
                LOGIN, json={"email": email, "password": NEW_PASSWORD}
            ).status_code
            == 200
        )
        assert (
            client.post(
                REFRESH, json={"refresh_token": foreign["refresh_token"]}
            ).status_code
            == 200
        )
        assert client.get("/api/v1/users/me", headers=headers).status_code == 200
        for value in (
            PASSWORD,
            NEW_PASSWORD,
            *(r.token_hash for r in rows),
            *(p["refresh_token"] for p in pairs),
        ):
            assert value not in caplog.text


@pytest.mark.parametrize(
    "case",
    ["wrong", "foreign_password", "expired_access", "same", "weak", "owner_override"],
)
def test_password_change_rejections_preserve_password_and_sessions(
    harness: Harness, case: str
) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        pair = client.post(LOGIN, json={"email": email, "password": PASSWORD}).json()
        body = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
        bearer = pair["access_token"]
        if case in {"wrong", "foreign_password"}:
            body["current_password"] = "test-only wrong password"
        if case == "foreign_password":
            other_email, _ = harness.register(client)
            foreign = client.post(
                LOGIN, json={"email": other_email, "password": PASSWORD}
            ).json()
            assert (
                client.post(
                    CHANGE_PASSWORD,
                    json={
                        "current_password": PASSWORD,
                        "new_password": "test-only other owner password",
                    },
                    headers={"Authorization": f"Bearer {foreign['access_token']}"},
                ).status_code
                == 204
            )
            body["current_password"] = "test-only other owner password"
        if case == "expired_access":
            bearer = create_access_token(
                owner, issued_at=datetime.now(UTC) - timedelta(days=1)
            )
        if case == "same":
            body["new_password"] = PASSWORD
        if case == "weak":
            body["new_password"] = "short"
        if case == "owner_override":
            body["user_id"] = str(uuid4())
        response = client.post(
            CHANGE_PASSWORD, json=body, headers={"Authorization": f"Bearer {bearer}"}
        )
        assert response.status_code == (
            422 if case in {"same", "weak", "owner_override"} else 401
        )
        assert harness.rows(owner)[0].revoked_at is None
        assert (
            client.post(LOGIN, json={"email": email, "password": PASSWORD}).status_code
            == 200
        )
        assert (
            client.post(
                LOGIN, json={"email": email, "password": NEW_PASSWORD}
            ).status_code
            == 401
        )


@pytest.mark.parametrize("stage", ["hash", "write", "revoke", "commit"])
def test_password_change_failure_rolls_back_both_changes(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        pair = client.post(LOGIN, json={"email": email, "password": PASSWORD}).json()
        with monkeypatch.context() as patch:
            if stage == "hash":
                from app.api.v1.endpoints import users

                def fail_hash(_: str) -> str:
                    raise RuntimeError(NEW_PASSWORD)

                patch.setattr(
                    users,
                    "change_password",
                    lambda c, u, s: change_password(c, u, s, password_hasher=fail_hash),
                )
            elif stage == "write":
                original_write = UserRepository.update_password

                def fail_write(
                    self: UserRepository,
                    user: User,
                    *,
                    password_hash: str,
                    updated_at: datetime,
                ) -> None:
                    original_write(
                        self, user, password_hash=password_hash, updated_at=updated_at
                    )
                    raise RuntimeError(NEW_PASSWORD)

                patch.setattr(UserRepository, "update_password", fail_write)
            elif stage == "revoke":
                original_revoke = RefreshTokenRepository.revoke_all_for_owner

                def fail_revoke(
                    self: RefreshTokenRepository, user_id: UUID, *, revoked_at: datetime
                ) -> None:
                    original_revoke(self, user_id, revoked_at=revoked_at)
                    raise RuntimeError(NEW_PASSWORD)

                patch.setattr(
                    RefreshTokenRepository, "revoke_all_for_owner", fail_revoke
                )
            else:

                def fail_commit(self: Session) -> None:
                    raise RuntimeError(NEW_PASSWORD)

                patch.setattr(Session, "commit", fail_commit)
            response = client.post(
                CHANGE_PASSWORD,
                json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
                headers={"Authorization": f"Bearer {pair['access_token']}"},
            )
        assert response.status_code == 503
        assert NEW_PASSWORD not in response.text
        assert harness.rows(owner)[0].revoked_at is None
        assert (
            client.post(LOGIN, json={"email": email, "password": PASSWORD}).status_code
            == 200
        )
        assert (
            client.post(
                LOGIN, json={"email": email, "password": NEW_PASSWORD}
            ).status_code
            == 401
        )
        assert (
            client.post(
                REFRESH, json={"refresh_token": pair["refresh_token"]}
            ).status_code
            == 200
        )


@pytest.mark.parametrize("state", ["expired", "future"])
def test_password_change_reloads_stale_user_and_handles_expired_future_rows(
    harness: Harness,
    state: str,
) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        client.post(LOGIN, json={"email": email, "password": PASSWORD})
        now = datetime.now(UTC)
        with harness.engine.begin() as connection:
            connection.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == owner)
                .values(
                    created_at=now + timedelta(days=1)
                    if state == "future"
                    else now - timedelta(days=8),
                    expires_at=now + timedelta(days=2)
                    if state == "future"
                    else now - timedelta(days=1),
                )
            )
        with Session(harness.engine) as stale:
            cached = stale.get(User, owner)
            assert cached is not None
            with Session(harness.engine) as winner:
                change_password(
                    PasswordChangeRequest.model_validate(
                        {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
                    ),
                    owner,
                    winner,
                )
            from app.core.exceptions import InvalidCredentialsError

            with pytest.raises(InvalidCredentialsError):
                change_password(
                    PasswordChangeRequest.model_validate(
                        {
                            "current_password": PASSWORD,
                            "new_password": "test-only third password",
                        }
                    ),
                    owner,
                    stale,
                )
        row = harness.rows(owner)[0]
        assert row.revoked_at is not None and row.revoked_at >= row.created_at
        if state == "future":
            assert row.revoked_at == row.created_at
        with Session(harness.engine) as session:
            current = session.get(User, owner)
            assert current is not None and verify_password(
                NEW_PASSWORD, current.password_hash
            )


def test_password_change_without_refresh_rows(harness: Harness) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        bearer = create_access_token(owner)
        response = client.post(
            CHANGE_PASSWORD,
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            headers={"Authorization": f"Bearer {bearer}"},
        )
        assert response.status_code == 204 and harness.rows(owner) == []
        assert (
            client.post(
                LOGIN, json={"email": email, "password": NEW_PASSWORD}
            ).status_code
            == 200
        )


@pytest.mark.parametrize(
    "first_kind,second_kind",
    [
        ("change", "login"),
        ("login", "change"),
        ("change", "refresh"),
        ("refresh", "change"),
        ("change", "change"),
        ("change", "new_login"),
    ],
)
def test_password_change_serializes_with_login_refresh_and_itself(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    first_kind: str,
    second_kind: str,
) -> None:
    with TestClient(harness.app) as client:
        email, owner = harness.register(client)
        pair = client.post(LOGIN, json={"email": email, "password": PASSWORD}).json()
    locked, release = Event(), Event()
    pids: Queue[int] = Queue()

    def sessions() -> Iterator[Session]:
        with Session(harness.engine) as session:
            pid = session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            pids.put(pid)
            yield session

    harness.app.dependency_overrides[get_session] = sessions

    def hold() -> None:
        if not locked.is_set():
            locked.set()
            assert release.wait(4)

    def request(kind: str) -> int:
        with TestClient(harness.app) as client:
            if kind in {"login", "new_login"}:
                return client.post(
                    LOGIN,
                    json={
                        "email": email,
                        "password": NEW_PASSWORD if kind == "new_login" else PASSWORD,
                    },
                ).status_code
            if kind == "refresh":
                return client.post(
                    REFRESH, json={"refresh_token": pair["refresh_token"]}
                ).status_code
            return client.post(
                CHANGE_PASSWORD,
                json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
                headers={"Authorization": f"Bearer {pair['access_token']}"},
            ).status_code

    with monkeypatch.context() as patch:
        if first_kind == "change":
            original_write = UserRepository.update_password

            def holding_write(
                self: UserRepository,
                user: User,
                *,
                password_hash: str,
                updated_at: datetime,
            ) -> None:
                original_write(
                    self, user, password_hash=password_hash, updated_at=updated_at
                )
                hold()

            patch.setattr(UserRepository, "update_password", holding_write)
        elif first_kind == "login":
            original_email = UserRepository.get_by_email_for_update

            def holding_email(self: UserRepository, email: str) -> User | None:
                result = original_email(self, email)
                hold()
                return result

            patch.setattr(UserRepository, "get_by_email_for_update", holding_email)
        else:
            original_refresh = RefreshTokenRepository.get_by_hash_for_update

            def holding_refresh(
                self: RefreshTokenRepository, token_hash: str
            ) -> RefreshToken | None:
                result = original_refresh(self, token_hash)
                hold()
                return result

            patch.setattr(
                RefreshTokenRepository, "get_by_hash_for_update", holding_refresh
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(request, first_kind)
            try:
                first_pid = pids.get(timeout=3)
                assert locked.wait(3)
                second = pool.submit(request, second_kind)
                second_pid = pids.get(timeout=3)
                deadline, blocked = monotonic() + 2, False
                with harness.engine.connect() as observer:
                    while monotonic() < deadline:
                        blockers = observer.scalar(
                            text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid}
                        )
                        if first_pid in blockers:
                            blocked = True
                            break
                        sleep(0.02)
                assert blocked
            finally:
                release.set()
            assert first.result(timeout=10) == (204 if first_kind == "change" else 200)
            expected = (
                200
                if second_kind == "new_login"
                else 401
                if first_kind == "change"
                else 204
            )
            assert second.result(timeout=10) == expected
    rows = harness.rows(owner)
    assert sum(r.revoked_at is None for r in rows) == (
        1 if second_kind == "new_login" else 0
    )
