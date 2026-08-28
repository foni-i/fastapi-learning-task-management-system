"""End-to-end registration tests against the dedicated PostgreSQL service."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier, Lock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.endpoints import auth
from app.core.email_normalization import normalize_email
from app.core.exceptions import DUPLICATE_EMAIL_MESSAGE
from app.core.security import verify_password
from app.db.session import get_session
from app.main import app
from app.models import User
from app.repositories.users import UserRepository
from app.schemas.user import PublicUser, UserRegistrationRequest
from app.services.registration import register_user as real_register_user

pytestmark = pytest.mark.integration

REGISTER_PATH = "/api/v1/auth/register"
TEST_PASSWORD = "integration password value"


class RegistrationHarness:
    """Provide real request Sessions plus exact committed-data cleanup."""

    def __init__(self, engine: Engine) -> None:
        self.session_factory = sessionmaker(
            bind=engine,
            class_=Session,
            expire_on_commit=False,
        )
        self.opened_sessions: list[Session] = []
        self.closed_sessions: list[Session] = []
        self.transaction_states_before_close: list[bool] = []
        self.test_emails: set[str] = set()
        self._lock = Lock()

    def session_dependency(self) -> Iterator[Session]:
        """Yield one independent Session and record its final lifecycle state."""

        session = self.session_factory()
        with self._lock:
            self.opened_sessions.append(session)
        try:
            yield session
        finally:
            transaction_active = session.in_transaction()
            session.close()
            with self._lock:
                self.transaction_states_before_close.append(transaction_active)
                self.closed_sessions.append(session)

    def track_email(self, raw_email: str) -> str:
        """Register one canonical test-owned email for exact cleanup."""

        email = normalize_email(raw_email)
        self.test_emails.add(email)
        return email

    def get_user(self, email: str) -> User | None:
        """Load one committed user through an independent read Session."""

        with self.session_factory() as session:
            return session.scalar(select(User).where(User.email == email))

    def count_users(self, email: str) -> int:
        """Count only records owned by the current integration test."""

        with self.session_factory() as session:
            count = session.scalar(
                select(func.count()).select_from(User).where(User.email == email)
            )
        assert count is not None
        return count

    def cleanup(self) -> None:
        """Delete only committed users explicitly registered by this harness."""

        if not self.test_emails:
            return
        with self.session_factory.begin() as session:
            session.execute(delete(User).where(User.email.in_(self.test_emails)))

    def assert_sessions_closed(self, expected_count: int) -> None:
        """Prove requests ended without active transactions or Session leaks."""

        assert len(self.opened_sessions) == expected_count
        assert {id(session) for session in self.opened_sessions} == {
            id(session) for session in self.closed_sessions
        }
        assert self.transaction_states_before_close == [False] * expected_count


@pytest.fixture
def registration_harness(integration_engine: Engine) -> Iterator[RegistrationHarness]:
    """Install the real request-Session override and always remove test data."""

    harness = RegistrationHarness(integration_engine)
    app.dependency_overrides[get_session] = harness.session_dependency
    try:
        yield harness
    finally:
        app.dependency_overrides.pop(get_session, None)
        harness.cleanup()


@pytest.fixture
def registration_client(
    registration_harness: RegistrationHarness,
) -> Iterator[TestClient]:
    """Create a public API client after the database override is installed."""

    assert registration_harness is not None
    with TestClient(app) as client:
        yield client


def assert_safe_output(
    output: str, plaintext: str, password_hash: str | None = None
) -> None:
    """Reject secrets, database URLs, and persistence diagnostics."""

    assert plaintext not in output
    if password_hash is not None:
        assert password_hash not in output
    assert "postgresql+psycopg" not in output
    assert "stms_test_local" not in output
    assert "integrityerror" not in output.casefold()


def test_registration_persists_canonical_user_and_safe_public_response(
    registration_client: TestClient,
    registration_harness: RegistrationHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Exercise the public route through commit and verify stored Argon2id data."""

    local_part = f"registration-success-{uuid4()}"
    raw_email = f"  {local_part}@EXAMPLE.COM "
    canonical_email = registration_harness.track_email(raw_email)

    response = registration_client.post(
        REGISTER_PATH,
        json={"email": raw_email, "password": TEST_PASSWORD},
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "email", "created_at", "updated_at"}
    assert UUID(body["id"])
    assert body["email"] == canonical_email
    for field in ("created_at", "updated_at"):
        timestamp = datetime.fromisoformat(body[field].replace("Z", "+00:00"))
        assert timestamp.tzinfo is not None
        utc_offset = timestamp.utcoffset()
        assert utc_offset is not None
        assert utc_offset.total_seconds() == 0

    user = registration_harness.get_user(canonical_email)
    assert user is not None
    assert user.id == UUID(body["id"])
    assert user.email == canonical_email
    assert user.password_hash != TEST_PASSWORD
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password(TEST_PASSWORD, user.password_hash) is True
    assert_safe_output(response.text, TEST_PASSWORD, user.password_hash)
    assert_safe_output(caplog.text, TEST_PASSWORD, user.password_hash)
    registration_harness.assert_sessions_closed(1)


def test_invalid_registration_returns_422_without_persisting_user(
    registration_client: TestClient,
    registration_harness: RegistrationHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stop weak input at the schema boundary without leaving a database row."""

    raw_email = f"registration-invalid-{uuid4()}@example.com"
    canonical_email = registration_harness.track_email(raw_email)
    rejected_password = "short value"

    response = registration_client.post(
        REGISTER_PATH,
        json={"email": raw_email, "password": rejected_password},
    )

    assert response.status_code == 422
    assert registration_harness.count_users(canonical_email) == 0
    assert_safe_output(response.text, rejected_password)
    assert_safe_output(caplog.text, rejected_password)
    registration_harness.assert_sessions_closed(1)


def test_sequential_canonical_duplicate_returns_safe_409_and_one_user(
    registration_client: TestClient,
    registration_harness: RegistrationHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep one row when equivalent email spellings arrive sequentially."""

    local_part = f"registration-duplicate-{uuid4()}"
    first_email = f"  {local_part}@EXAMPLE.COM "
    second_email = f"{local_part}@example.com"
    canonical_email = registration_harness.track_email(first_email)
    assert canonical_email == normalize_email(second_email)

    first_response = registration_client.post(
        REGISTER_PATH,
        json={"email": first_email, "password": TEST_PASSWORD},
    )
    second_response = registration_client.post(
        REGISTER_PATH,
        json={"email": second_email, "password": TEST_PASSWORD},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409
    assert second_response.json() == {"detail": DUPLICATE_EMAIL_MESSAGE}
    assert registration_harness.count_users(canonical_email) == 1
    assert canonical_email not in second_response.text
    assert "uq_users_email" not in second_response.text
    assert "sql" not in second_response.text.casefold()
    assert_safe_output(second_response.text, TEST_PASSWORD)
    assert_safe_output(caplog.text, TEST_PASSWORD)
    registration_harness.assert_sessions_closed(2)


def test_concurrent_registration_uses_named_constraint_as_final_defense(
    registration_harness: RegistrationHarness,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Synchronize stale checks and prove one 201, one safe 409, and no leaks."""

    local_part = f"registration-concurrent-{uuid4()}"
    raw_email = f"{local_part}@example.com"
    canonical_email = registration_harness.track_email(raw_email)
    lookup_barrier = Barrier(2, timeout=10)
    observation_lock = Lock()
    connection_ids: set[int] = set()
    observed_constraints: list[str | None] = []

    class BarrierUserRepository(UserRepository):
        """Pause both real misses before allowing either insert to proceed."""

        def get_by_email(self, email: str) -> User | None:
            user = super().get_by_email(email)
            with observation_lock:
                connection_ids.add(id(self._session.connection()))
            lookup_barrier.wait()
            return user

        def create(self, *, email: str, password_hash: str) -> User:
            try:
                return super().create(email=email, password_hash=password_hash)
            except IntegrityError as error:
                diagnostic = getattr(error.orig, "diag", None)
                with observation_lock:
                    observed_constraints.append(
                        getattr(diagnostic, "constraint_name", None)
                    )
                raise

    def register_with_barrier(
        registration: UserRegistrationRequest,
        session: Session,
    ) -> PublicUser:
        return real_register_user(
            registration,
            session,
            repository_factory=BarrierUserRepository,
        )

    monkeypatch.setattr(auth, "register_user", register_with_barrier)

    def send_request(client: TestClient) -> tuple[int, str]:
        response = client.post(
            REGISTER_PATH,
            json={"email": raw_email, "password": TEST_PASSWORD},
        )
        return response.status_code, response.text

    with (
        TestClient(app) as first_client,
        TestClient(app) as second_client,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        futures = [
            executor.submit(send_request, first_client),
            executor.submit(send_request, second_client),
        ]
        results = [future.result(timeout=20) for future in futures]

    assert sorted(status_code for status_code, _text in results) == [201, 409]
    conflict_text = next(text for status_code, text in results if status_code == 409)
    assert conflict_text == f'{{"detail":"{DUPLICATE_EMAIL_MESSAGE}"}}'
    assert len(connection_ids) == 2
    assert observed_constraints == ["uq_users_email"]
    assert registration_harness.count_users(canonical_email) == 1
    assert canonical_email not in conflict_text
    assert "uq_users_email" not in conflict_text
    assert_safe_output(conflict_text, TEST_PASSWORD)
    assert_safe_output(caplog.text, TEST_PASSWORD)
    registration_harness.assert_sessions_closed(2)
