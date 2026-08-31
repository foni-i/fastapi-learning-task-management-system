"""End-to-end Stage 4 authentication tests against dedicated PostgreSQL."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.endpoints import users
from app.core.config import get_settings
from app.core.email_normalization import normalize_email
from app.core.exceptions import (
    AUTHENTICATION_REQUIRED_MESSAGE,
    DUPLICATE_EMAIL_MESSAGE,
    INVALID_CREDENTIALS_MESSAGE,
)
from app.core.security import verify_password
from app.core.tokens import (
    ACCESS_TOKEN_ALGORITHM,
    ACCESS_TOKEN_TYPE,
    create_access_token,
)
from app.db.session import get_session
from app.main import app
from app.models import User
from app.repositories.users import UserRepository
from app.schemas.user import CurrentUserEmailUpdate, PublicUser
from app.services.current_user import (
    update_current_user_email as real_update_current_user_email,
)

pytestmark = pytest.mark.integration

REGISTER_PATH = "/api/v1/auth/register"
LOGIN_PATH = "/api/v1/auth/login"
CURRENT_USER_PATH = "/api/v1/users/me"
TEST_PASSWORD = "authentication integration password"
WRONG_PASSWORD = "different integration password"


class AuthenticationHarness:
    """Provide request Sessions, persisted-state inspection, and exact cleanup."""

    def __init__(self, engine: Engine) -> None:
        self.session_factory = sessionmaker(
            bind=engine,
            class_=Session,
            expire_on_commit=False,
        )
        self.opened_sessions: list[Session] = []
        self.closed_sessions: list[Session] = []
        self.test_emails: set[str] = set()
        self.test_user_ids: set[UUID] = set()
        self._lock = Lock()

    def session_dependency(self) -> Iterator[Session]:
        """Yield one independent request Session and always close it."""

        session = self.session_factory()
        with self._lock:
            self.opened_sessions.append(session)
        try:
            yield session
        finally:
            session.close()
            with self._lock:
                self.closed_sessions.append(session)

    def track_email(self, raw_email: str) -> str:
        """Record one canonical email that this test may persist."""

        email = normalize_email(raw_email)
        self.test_emails.add(email)
        return email

    def track_user(self, user_id: str) -> UUID:
        """Record one test-owned UUID for cleanup after email mutations."""

        parsed_id = UUID(user_id)
        self.test_user_ids.add(parsed_id)
        return parsed_id

    def get_user(self, user_id: UUID) -> SimpleNamespace | None:
        """Return a detached snapshot of one committed user."""

        with self.session_factory() as session:
            user = session.scalar(select(User).where(User.id == user_id))
            if user is None:
                return None
            return SimpleNamespace(
                id=user.id,
                email=user.email,
                password_hash=user.password_hash,
                created_at=user.created_at,
                updated_at=user.updated_at,
            )

    def count_email(self, email: str) -> int:
        """Count only rows for one canonical test email."""

        with self.session_factory() as session:
            count = session.scalar(
                select(func.count()).select_from(User).where(User.email == email)
            )
        assert count is not None
        return count

    def cleanup(self) -> None:
        """Delete only rows explicitly owned by the current test."""

        predicates = []
        if self.test_user_ids:
            predicates.append(User.id.in_(self.test_user_ids))
        if self.test_emails:
            predicates.append(User.email.in_(self.test_emails))
        if not predicates:
            return
        with self.session_factory.begin() as session:
            session.execute(delete(User).where(or_(*predicates)))

    def session_marker(self) -> int:
        """Return the current request-Session count."""

        with self._lock:
            return len(self.opened_sessions)

    def assert_sessions_closed_since(self, marker: int, expected_count: int) -> None:
        """Prove the expected request Sessions opened and all were closed."""

        with self._lock:
            opened = self.opened_sessions[marker:]
            closed_ids = {id(session) for session in self.closed_sessions}
        assert len(opened) == expected_count
        assert all(id(session) in closed_ids for session in opened)


@pytest.fixture
def authentication_harness(
    integration_engine: Engine,
) -> Iterator[AuthenticationHarness]:
    """Install a real request-Session override and remove committed test rows."""

    harness = AuthenticationHarness(integration_engine)
    app.dependency_overrides[get_session] = harness.session_dependency
    try:
        yield harness
    finally:
        app.dependency_overrides.pop(get_session, None)
        harness.cleanup()


@pytest.fixture
def authentication_client(
    authentication_harness: AuthenticationHarness,
) -> Iterator[TestClient]:
    """Expose the public API after installing the database override."""

    assert authentication_harness is not None
    with TestClient(app) as client:
        yield client


def register_user(
    client: TestClient,
    harness: AuthenticationHarness,
    raw_email: str,
) -> tuple[UUID, str]:
    """Register one test-owned user through the public API."""

    canonical_email = harness.track_email(raw_email)
    response = client.post(
        REGISTER_PATH,
        json={"email": raw_email, "password": TEST_PASSWORD},
    )
    assert response.status_code == 201
    user_id = harness.track_user(response.json()["id"])
    return user_id, canonical_email


def login(client: TestClient, email: str, password: str = TEST_PASSWORD) -> str:
    """Authenticate through the public API and return the expected secret output."""

    response = client.post(
        LOGIN_PATH,
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    assert set(response.json()) == {"access_token", "token_type"}
    assert response.json()["token_type"] == "bearer"
    return cast(str, response.json()["access_token"])


def bearer(token: str) -> dict[str, str]:
    """Build one request header without logging its credential."""

    return {"Authorization": f"Bearer {token}"}


def assert_safe_failure(
    response_text: str,
    caplog_text: str,
    *sensitive_values: str,
) -> None:
    """Reject secret and persistence diagnostics from failure surfaces."""

    for value in sensitive_values:
        assert value not in response_text
        assert value not in caplog_text
    combined = f"{response_text}\n{caplog_text}".casefold()
    assert "password_hash" not in combined
    assert "postgresql+psycopg" not in combined
    assert "integrityerror" not in combined
    assert "uq_users_email" not in combined


def encode_test_claims(**overrides: object) -> str:
    """Sign controlled claims with the configured synthetic integration secret."""

    settings = get_settings()
    secret = settings.access_token_secret
    assert secret is not None
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "sub": str(uuid4()),
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": settings.access_token_issuer,
        "aud": settings.access_token_audience,
    }
    payload.update(overrides)
    return jwt.encode(
        payload,
        secret.get_secret_value(),
        algorithm=ACCESS_TOKEN_ALGORITHM,
    )


def test_real_login_current_user_and_canonical_email_update(
    authentication_client: TestClient,
    authentication_harness: AuthenticationHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Prove registration through login and UUID-bound profile mutation."""

    marker = authentication_harness.session_marker()
    local_part = f"authentication-flow-{uuid4()}"
    raw_email = f"  {local_part}@EXAMPLE.COM "
    user_id, canonical_email = register_user(
        authentication_client,
        authentication_harness,
        raw_email,
    )
    original = authentication_harness.get_user(user_id)
    assert original is not None
    assert original.email == canonical_email
    assert original.password_hash != TEST_PASSWORD
    assert original.password_hash.startswith("$argon2id$")
    assert verify_password(TEST_PASSWORD, original.password_hash) is True

    token = login(authentication_client, canonical_email)
    current_response = authentication_client.get(
        CURRENT_USER_PATH,
        headers=bearer(token),
    )
    assert current_response.status_code == 200
    assert set(current_response.json()) == {"id", "email", "created_at", "updated_at"}
    assert UUID(current_response.json()["id"]) == user_id

    updated_raw_email = f"  UPDATED-{local_part}@EXAMPLE.COM "
    updated_email = authentication_harness.track_email(updated_raw_email)
    update_response = authentication_client.patch(
        CURRENT_USER_PATH,
        headers=bearer(token),
        json={"email": updated_raw_email},
    )
    assert update_response.status_code == 200
    assert set(update_response.json()) == {"id", "email", "created_at", "updated_at"}
    assert update_response.json()["email"] == updated_email

    updated = authentication_harness.get_user(user_id)
    assert updated is not None
    assert updated.email == updated_email
    assert updated.id == original.id
    assert updated.password_hash == original.password_hash
    assert updated.created_at == original.created_at

    old_login = authentication_client.post(
        LOGIN_PATH,
        json={"email": canonical_email, "password": TEST_PASSWORD},
    )
    assert old_login.status_code == 401
    assert old_login.json() == {"detail": INVALID_CREDENTIALS_MESSAGE}
    assert old_login.headers["www-authenticate"] == "Bearer"

    new_token = login(authentication_client, updated_email)
    assert new_token != ""
    old_token_response = authentication_client.get(
        CURRENT_USER_PATH,
        headers=bearer(token),
    )
    assert old_token_response.status_code == 200
    assert UUID(old_token_response.json()["id"]) == user_id
    assert old_token_response.json()["email"] == updated_email

    assert TEST_PASSWORD not in caplog.text
    assert original.password_hash not in caplog.text
    assert token not in caplog.text
    assert new_token not in caplog.text
    authentication_harness.assert_sessions_closed_since(marker, 7)


def test_unknown_email_and_wrong_password_share_safe_login_401(
    authentication_client: TestClient,
    authentication_harness: AuthenticationHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Prevent account enumeration through the real login path."""

    marker = authentication_harness.session_marker()
    raw_email = f"authentication-login-{uuid4()}@example.com"
    _user_id, canonical_email = register_user(
        authentication_client,
        authentication_harness,
        raw_email,
    )
    missing_email = f"missing-{uuid4()}@example.com"

    missing_response = authentication_client.post(
        LOGIN_PATH,
        json={"email": missing_email, "password": TEST_PASSWORD},
    )
    wrong_response = authentication_client.post(
        LOGIN_PATH,
        json={"email": canonical_email, "password": WRONG_PASSWORD},
    )

    for response in (missing_response, wrong_response):
        assert response.status_code == 401
        assert response.json() == {"detail": INVALID_CREDENTIALS_MESSAGE}
        assert response.headers["www-authenticate"] == "Bearer"
        assert_safe_failure(
            response.text,
            caplog.text,
            TEST_PASSWORD,
            WRONG_PASSWORD,
            missing_email,
            canonical_email,
        )
    authentication_harness.assert_sessions_closed_since(marker, 3)


def test_invalid_and_unknown_subject_tokens_share_safe_bearer_401(
    authentication_client: TestClient,
    authentication_harness: AuthenticationHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reject every invalid token class before exposing persisted identity."""

    marker = authentication_harness.session_marker()
    user_id, email = register_user(
        authentication_client,
        authentication_harness,
        f"authentication-token-{uuid4()}@example.com",
    )
    valid_token = login(authentication_client, email)
    settings = get_settings()
    now = datetime.now(UTC)
    invalid_tokens = {
        "expired": encode_test_claims(exp=now - timedelta(seconds=1)),
        "tampered": jwt.encode(
            {
                "sub": str(user_id),
                "type": ACCESS_TOKEN_TYPE,
                "iat": now,
                "exp": now + timedelta(minutes=5),
                "iss": settings.access_token_issuer,
                "aud": settings.access_token_audience,
            },
            "different-synthetic-integration-secret-value",
            algorithm=ACCESS_TOKEN_ALGORITHM,
        ),
        "wrong-type": encode_test_claims(sub=str(user_id), type="refresh"),
        "wrong-issuer": encode_test_claims(sub=str(user_id), iss="wrong-issuer"),
        "wrong-audience": encode_test_claims(
            sub=str(user_id),
            aud="wrong-audience",
        ),
        "invalid-subject": encode_test_claims(sub="not-a-uuid"),
        "unknown-subject": create_access_token(uuid4()),
    }

    for case, token in invalid_tokens.items():
        response = authentication_client.get(
            CURRENT_USER_PATH,
            headers=bearer(token),
        )
        assert response.status_code == 401, case
        assert response.json() == {"detail": AUTHENTICATION_REQUIRED_MESSAGE}, case
        assert response.headers["www-authenticate"] == "Bearer", case
        assert_safe_failure(response.text, caplog.text, token)

    assert valid_token not in caplog.text
    authentication_harness.assert_sessions_closed_since(marker, 9)


def test_duplicate_email_update_returns_safe_409_and_preserves_both_users(
    authentication_client: TestClient,
    authentication_harness: AuthenticationHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Prove the friendly duplicate path rolls back without state loss."""

    marker = authentication_harness.session_marker()
    first_id, first_email = register_user(
        authentication_client,
        authentication_harness,
        f"authentication-first-{uuid4()}@example.com",
    )
    second_id, second_email = register_user(
        authentication_client,
        authentication_harness,
        f"authentication-second-{uuid4()}@example.com",
    )
    second_token = login(authentication_client, second_email)

    response = authentication_client.patch(
        CURRENT_USER_PATH,
        headers=bearer(second_token),
        json={"email": f"  {first_email.upper()} "},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": DUPLICATE_EMAIL_MESSAGE}
    first_user = authentication_harness.get_user(first_id)
    second_user = authentication_harness.get_user(second_id)
    assert first_user is not None
    assert second_user is not None
    assert first_user.email == first_email
    assert second_user.email == second_email
    assert authentication_harness.count_email(first_email) == 1
    assert authentication_harness.count_email(second_email) == 1
    assert_safe_failure(
        response.text,
        caplog.text,
        TEST_PASSWORD,
        first_email,
        second_email,
        second_token,
    )
    authentication_harness.assert_sessions_closed_since(marker, 4)


def test_concurrent_email_updates_use_named_constraint_as_final_defense(
    authentication_harness: AuthenticationHarness,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Force two stale checks and prove one 200, one 409, and no Session leak."""

    with TestClient(app) as setup_client:
        first_id, first_email = register_user(
            setup_client,
            authentication_harness,
            f"authentication-race-first-{uuid4()}@example.com",
        )
        second_id, second_email = register_user(
            setup_client,
            authentication_harness,
            f"authentication-race-second-{uuid4()}@example.com",
        )
        first_token = login(setup_client, first_email)
        second_token = login(setup_client, second_email)

    marker = authentication_harness.session_marker()
    target_email = authentication_harness.track_email(
        f"authentication-race-target-{uuid4()}@example.com"
    )
    lookup_barrier = Barrier(2, timeout=10)
    observation_lock = Lock()
    connection_ids: set[int] = set()
    observed_constraints: list[str | None] = []

    class BarrierUserRepository(UserRepository):
        """Pause both real misses before either email update may flush."""

        def get_by_email(self, email: str) -> User | None:
            user = super().get_by_email(email)
            with observation_lock:
                connection_ids.add(id(self._session.connection()))
            lookup_barrier.wait()
            return user

        def update_email(self, user: User, *, email: str) -> User:
            try:
                return super().update_email(user, email=email)
            except IntegrityError as error:
                diagnostic = getattr(error.orig, "diag", None)
                with observation_lock:
                    observed_constraints.append(
                        getattr(diagnostic, "constraint_name", None)
                    )
                raise

    def update_with_barrier(
        update: CurrentUserEmailUpdate,
        current_user: User,
        session: Session,
    ) -> PublicUser:
        return real_update_current_user_email(
            update,
            current_user,
            session,
            repository_factory=BarrierUserRepository,
        )

    monkeypatch.setattr(users, "update_current_user_email", update_with_barrier)

    def send_request(token: str) -> tuple[int, str]:
        with TestClient(app) as client:
            response = client.patch(
                CURRENT_USER_PATH,
                headers=bearer(token),
                json={"email": target_email},
            )
        return response.status_code, response.text

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(send_request, first_token),
            executor.submit(send_request, second_token),
        ]
        results = [future.result(timeout=20) for future in futures]

    assert sorted(status for status, _text in results) == [200, 409]
    conflict_text = next(text for status, text in results if status == 409)
    assert conflict_text == f'{{"detail":"{DUPLICATE_EMAIL_MESSAGE}"}}'
    assert len(connection_ids) == 2
    assert observed_constraints == ["uq_users_email"]
    assert authentication_harness.count_email(target_email) == 1
    assert (
        authentication_harness.count_email(first_email)
        + authentication_harness.count_email(second_email)
        == 1
    )
    assert authentication_harness.get_user(first_id) is not None
    assert authentication_harness.get_user(second_id) is not None
    assert_safe_failure(
        conflict_text,
        caplog.text,
        TEST_PASSWORD,
        target_email,
        first_token,
        second_token,
    )
    authentication_harness.assert_sessions_closed_since(marker, 2)
