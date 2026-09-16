"""Real PostgreSQL document ingestion, ownership, and constraints."""

from collections.abc import Iterator
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    KNOWLEDGE_DOCUMENT_INVALID_MESSAGE,
    KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE,
)
from app.db.session import get_session
from app.main import app
from app.models import KnowledgeDocument
from tests.fakes.pdf import minimal_text_pdf
from tests.integration.test_authentication import (
    AuthenticationHarness,
    bearer,
    login,
    register_user,
)

pytestmark = pytest.mark.integration

PATH = "/api/v1/knowledge/documents"
PUBLIC_FIELDS = {
    "id",
    "display_name",
    "media_type",
    "byte_count",
    "page_count",
    "status",
    "created_at",
    "updated_at",
}


class KnowledgeDocumentHarness(AuthenticationHarness):
    """Track committed documents and delete only rows created here."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine)
        self.document_ids: set[UUID] = set()

    def track(self, body: dict[str, object]) -> UUID:
        document_id = UUID(str(body["id"]))
        self.document_ids.add(document_id)
        return document_id

    def snapshot(self, document_id: UUID) -> SimpleNamespace | None:
        with self.session_factory() as session:
            document = session.get(KnowledgeDocument, document_id)
            if document is None:
                return None
            return SimpleNamespace(
                id=document.id,
                user_id=document.user_id,
                display_name=document.display_name,
                media_type=document.media_type,
                byte_count=document.byte_count,
                content_sha256=document.content_sha256,
                page_count=document.page_count,
                extracted_text=document.extracted_text,
                status=document.status,
            )

    def count_owned(self, user_id: UUID) -> int:
        with self.session_factory() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(KnowledgeDocument)
                    .where(KnowledgeDocument.user_id == user_id)
                )
                or 0
            )

    def cleanup(self) -> None:
        if self.document_ids:
            with self.session_factory.begin() as session:
                session.execute(
                    delete(KnowledgeDocument).where(
                        KnowledgeDocument.id.in_(self.document_ids)
                    )
                )
        super().cleanup()


@pytest.fixture
def document_harness(
    integration_engine: Engine,
) -> Iterator[KnowledgeDocumentHarness]:
    harness = KnowledgeDocumentHarness(integration_engine)
    app.dependency_overrides[get_session] = harness.session_dependency
    try:
        yield harness
    finally:
        app.dependency_overrides.pop(get_session, None)
        assert {id(item) for item in harness.opened_sessions} == {
            id(item) for item in harness.closed_sessions
        }
        harness.cleanup()


@pytest.fixture
def document_client(
    document_harness: KnowledgeDocumentHarness,
) -> Iterator[TestClient]:
    assert document_harness is not None
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize(
    ("filename", "media_type", "content", "expected_text"),
    [
        ("notes.txt", "text/plain", b"Study notes", "Study notes"),
        ("plan.md", "text/markdown", b"# Study plan", "# Study plan"),
        ("syllabus.pdf", "application/pdf", minimal_text_pdf(), "Study syllabus"),
    ],
    ids=("text", "markdown", "pdf"),
)
def test_upload_persists_private_text_and_returns_public_metadata(
    document_client: TestClient,
    document_harness: KnowledgeDocumentHarness,
    filename: str,
    media_type: str,
    content: bytes,
    expected_text: str,
) -> None:
    owner_id, email = register_user(
        document_client,
        document_harness,
        f"document-{uuid4()}@example.com",
    )
    token = login(document_client, email)

    response = document_client.post(
        PATH,
        headers=bearer(token),
        files={"file": (filename, content, media_type)},
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == PUBLIC_FIELDS
    document_id = document_harness.track(body)
    stored = document_harness.snapshot(document_id)
    assert stored is not None
    assert stored.user_id == owner_id
    assert stored.extracted_text == expected_text
    assert stored.content_sha256 not in response.text
    assert expected_text not in response.text
    assert document_harness.count_owned(owner_id) == 1


def test_detail_is_owner_scoped_and_invalid_upload_leaves_no_row(
    document_client: TestClient,
    document_harness: KnowledgeDocumentHarness,
) -> None:
    owner_id, owner_email = register_user(
        document_client,
        document_harness,
        f"document-owner-{uuid4()}@example.com",
    )
    _other_id, other_email = register_user(
        document_client,
        document_harness,
        f"document-other-{uuid4()}@example.com",
    )
    owner_token = login(document_client, owner_email)
    other_token = login(document_client, other_email)
    created = document_client.post(
        PATH,
        headers=bearer(owner_token),
        files={"file": ("private.txt", b"private material", "text/plain")},
    )
    assert created.status_code == 201
    document_id = document_harness.track(created.json())

    own = document_client.get(f"{PATH}/{document_id}", headers=bearer(owner_token))
    foreign = document_client.get(f"{PATH}/{document_id}", headers=bearer(other_token))
    missing = document_client.get(f"{PATH}/{uuid4()}", headers=bearer(other_token))
    assert own.status_code == 200
    assert foreign.status_code == missing.status_code == 404
    assert (
        foreign.json()
        == missing.json()
        == {"detail": KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE}
    )

    invalid = document_client.post(
        PATH,
        headers=bearer(owner_token),
        files={"file": ("bad.pdf", b"private malformed bytes", "application/pdf")},
    )
    assert invalid.status_code == 422
    assert "private malformed bytes" not in invalid.text
    assert document_harness.count_owned(owner_id) == 1


def test_nul_upload_leaves_no_row_and_session_remains_usable(
    document_client: TestClient,
    document_harness: KnowledgeDocumentHarness,
) -> None:
    owner_id, email = register_user(
        document_client,
        document_harness,
        f"document-nul-{uuid4()}@example.com",
    )
    token = login(document_client, email)
    assert document_harness.count_owned(owner_id) == 0

    rejected = document_client.post(
        PATH,
        headers=bearer(token),
        files={"file": ("unsafe.txt", b"private\x00material", "text/plain")},
    )

    assert rejected.status_code == 422
    assert rejected.json() == {"detail": KNOWLEDGE_DOCUMENT_INVALID_MESSAGE}
    assert "private" not in rejected.text
    assert document_harness.count_owned(owner_id) == 0

    accepted = document_client.post(
        PATH,
        headers=bearer(token),
        files={"file": ("safe.txt", b"safe material", "text/plain")},
    )
    assert accepted.status_code == 201
    document_harness.track(accepted.json())
    assert document_harness.count_owned(owner_id) == 1


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("display_name", "   ", "ck_knowledge_documents_display_name_not_blank"),
        ("media_type", "text/html", "ck_knowledge_documents_media_type"),
        ("byte_count", 0, "ck_knowledge_documents_byte_count"),
        ("page_count", 0, "ck_knowledge_documents_page_count"),
        ("extracted_text", "", "ck_knowledge_documents_extracted_text_length"),
        ("content_sha256", "invalid", "ck_knowledge_documents_content_sha256"),
        ("status", "FAILED", "ck_knowledge_documents_status"),
    ],
)
def test_named_constraints_reject_invalid_direct_writes(
    document_client: TestClient,
    document_harness: KnowledgeDocumentHarness,
    field: str,
    value: object,
    constraint: str,
) -> None:
    user_id, _email = register_user(
        document_client,
        document_harness,
        f"document-constraint-{uuid4()}@example.com",
    )
    values: dict[str, object] = {
        "user_id": user_id,
        "display_name": "notes.txt",
        "media_type": "text/plain",
        "byte_count": 5,
        "content_sha256": "a" * 64,
        "page_count": 1,
        "extracted_text": "notes",
        "status": "PARSED",
        field: value,
    }
    with document_harness.session_factory() as session:
        with pytest.raises(IntegrityError) as exc_info:
            session.execute(insert(KnowledgeDocument).values(**values))
            session.flush()
        diagnostic = getattr(exc_info.value.orig, "diag", None)
        assert getattr(diagnostic, "constraint_name", None) == constraint
        session.rollback()
