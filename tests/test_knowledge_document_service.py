"""Tests for bounded parsing and Service-owned document transactions."""

from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy.orm import Session

from app.core.exceptions import (
    KNOWLEDGE_DOCUMENT_INVALID_MESSAGE,
    KnowledgeDocumentInvalidError,
    KnowledgeDocumentNotFoundError,
    KnowledgeDocumentTooLargeError,
)
from app.models.knowledge_document import KnowledgeDocument
from app.repositories.knowledge_documents import KnowledgeDocumentRepository
from app.services import knowledge_documents as knowledge_document_service
from app.services.knowledge_documents import (
    DOCUMENT_PAGE_SEPARATOR,
    MAX_DOCUMENT_BYTES,
    MAX_DOCUMENT_NAME_CHARACTERS,
    MAX_DOCUMENT_TEXT_CHARACTERS,
    create_knowledge_document,
    get_owned_knowledge_document,
    parse_knowledge_document,
)
from tests.fakes.pdf import minimal_text_pdf


@pytest.mark.parametrize(
    ("filename", "media_type", "content", "expected_text"),
    [
        ("notes.txt", "text/plain", b"  Study notes\r\n", "Study notes"),
        ("plan.md", "text/markdown", b"# Plan\n", "# Plan"),
        ("course.pdf", "application/pdf", minimal_text_pdf(), "Study syllabus"),
    ],
    ids=("text", "markdown", "pdf"),
)
def test_parser_accepts_three_supported_local_formats(
    filename: str,
    media_type: str,
    content: bytes,
    expected_text: str,
) -> None:
    parsed = parse_knowledge_document(
        filename=filename,
        media_type=media_type,
        content=content,
    )

    assert expected_text in parsed.extracted_text
    assert parsed.page_count == 1
    assert parsed.byte_count == len(content)
    assert len(parsed.content_sha256) == 64


@pytest.mark.parametrize(
    ("filename", "media_type", "content"),
    [
        ("notes.exe", "text/plain", b"notes"),
        ("notes.txt", "text/markdown", b"notes"),
        ("fake.pdf", "application/pdf", b"not a pdf"),
        ("fake.txt", "text/plain", minimal_text_pdf()),
        ("blank.md", "text/markdown", b"   \n"),
        ("broken.txt", "text/plain", b"\xff\xfe"),
        ("unsafe\x00name.txt", "text/plain", b"notes"),
        ("unsafe.txt", "text/plain", b"notes\x00private"),
        ("unsafe.md", "text/markdown", b"# plan\x00private"),
        ("x" * (MAX_DOCUMENT_NAME_CHARACTERS + 1) + ".txt", "text/plain", b"x"),
    ],
    ids=(
        "extension",
        "mime",
        "pdf-signature",
        "disguised-pdf",
        "blank",
        "utf8",
        "nul-name",
        "nul-text",
        "nul-markdown",
        "name",
    ),
)
def test_parser_rejects_invalid_input_without_echo(
    filename: str,
    media_type: str,
    content: bytes,
) -> None:
    with pytest.raises(KnowledgeDocumentInvalidError) as exc_info:
        parse_knowledge_document(
            filename=filename,
            media_type=media_type,
            content=content,
        )

    assert str(exc_info.value) == KNOWLEDGE_DOCUMENT_INVALID_MESSAGE
    assert filename not in str(exc_info.value)


def test_parser_enforces_byte_and_extracted_text_limits() -> None:
    with pytest.raises(KnowledgeDocumentTooLargeError):
        parse_knowledge_document(
            filename="large.txt",
            media_type="text/plain",
            content=b"x" * (MAX_DOCUMENT_BYTES + 1),
        )
    with pytest.raises(KnowledgeDocumentInvalidError):
        parse_knowledge_document(
            filename="long.txt",
            media_type="text/plain",
            content=b"x" * (MAX_DOCUMENT_TEXT_CHARACTERS + 1),
        )


def test_parser_rejects_encrypted_and_over_page_limit_pdfs() -> None:
    encrypted_writer = PdfWriter()
    encrypted_writer.add_blank_page(width=72, height=72)
    encrypted_writer.encrypt("synthetic-test-password")
    encrypted = BytesIO()
    encrypted_writer.write(encrypted)

    many_pages_writer = PdfWriter()
    for _ in range(101):
        many_pages_writer.add_blank_page(width=72, height=72)
    many_pages = BytesIO()
    many_pages_writer.write(many_pages)

    for content in (encrypted.getvalue(), many_pages.getvalue()):
        with pytest.raises(KnowledgeDocumentInvalidError):
            parse_knowledge_document(
                filename="bounded.pdf",
                media_type="application/pdf",
                content=content,
            )


def test_pdf_parser_preserves_deterministic_page_boundaries() -> None:
    writer = PdfWriter()
    for value in ("First page", "Second page"):
        reader = PdfReader(BytesIO(minimal_text_pdf(value)))
        writer.add_page(reader.pages[0])
    combined = BytesIO()
    writer.write(combined)

    parsed = parse_knowledge_document(
        filename="two-pages.pdf",
        media_type="application/pdf",
        content=combined.getvalue(),
    )

    assert parsed.page_count == 2
    assert parsed.extracted_text.split(DOCUMENT_PAGE_SEPARATOR) == [
        "First page",
        "Second page",
    ]


def test_pdf_parser_rejects_nul_from_each_extracted_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SimpleNamespace(extract_text=lambda: "private\x00page")
    reader = SimpleNamespace(is_encrypted=False, pages=[page])
    monkeypatch.setattr(
        knowledge_document_service,
        "PdfReader",
        lambda *_args, **_kwargs: reader,
    )

    with pytest.raises(KnowledgeDocumentInvalidError) as exc_info:
        parse_knowledge_document(
            filename="unsafe.pdf",
            media_type="application/pdf",
            content=b"%PDF-controlled",
        )

    assert str(exc_info.value) == KNOWLEDGE_DOCUMENT_INVALID_MESSAGE


def test_nul_is_rejected_before_repository_construction() -> None:
    session = MagicMock(spec=Session)
    factory = MagicMock()

    with pytest.raises(KnowledgeDocumentInvalidError):
        create_knowledge_document(
            filename="unsafe.txt",
            media_type="text/plain",
            content=b"private\x00text",
            user_id=uuid4(),
            session=session,
            repository_factory=factory,
        )

    factory.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def _document(user_id: UUID) -> KnowledgeDocument:
    now = datetime.now(UTC)
    return cast(
        KnowledgeDocument,
        SimpleNamespace(
            id=uuid4(),
            user_id=user_id,
            display_name="notes.txt",
            media_type="text/plain",
            byte_count=5,
            content_sha256="a" * 64,
            page_count=1,
            extracted_text="notes",
            status="PARSED",
            created_at=now,
            updated_at=now,
        ),
    )


class ControlledRepository:
    def __init__(
        self,
        document: KnowledgeDocument | None,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.document = document
        self.failure = failure
        self.created: dict[str, object] | None = None
        self.lookup: tuple[UUID, UUID] | None = None

    def create(self, **values: object) -> KnowledgeDocument:
        self.created = values
        if self.failure is not None:
            raise self.failure
        assert self.document is not None
        return self.document

    def get_owned_by_id(
        self, *, document_id: UUID, user_id: UUID
    ) -> KnowledgeDocument | None:
        self.lookup = (document_id, user_id)
        return self.document


def repository_factory(
    repository: ControlledRepository,
) -> type[KnowledgeDocumentRepository]:
    return cast(type[KnowledgeDocumentRepository], lambda _session: repository)


def test_create_derives_owner_commits_and_returns_public_only() -> None:
    session = MagicMock(spec=Session)
    user_id = uuid4()
    repository = ControlledRepository(_document(user_id))

    result = create_knowledge_document(
        filename="notes.txt",
        media_type="text/plain",
        content=b"notes",
        user_id=user_id,
        session=session,
        repository_factory=repository_factory(repository),
    )

    assert repository.created is not None
    assert repository.created["user_id"] == user_id
    assert repository.created["extracted_text"] == "notes"
    assert "user_id" not in result.model_dump()
    assert "extracted_text" not in result.model_dump()
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


def test_create_rolls_back_unexpected_persistence_failure() -> None:
    session = MagicMock(spec=Session)
    failure = RuntimeError("controlled failure")
    repository = ControlledRepository(None, failure=failure)

    with pytest.raises(RuntimeError) as exc_info:
        create_knowledge_document(
            filename="notes.txt",
            media_type="text/plain",
            content=b"notes",
            user_id=uuid4(),
            session=session,
            repository_factory=repository_factory(repository),
        )

    assert exc_info.value is failure
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_owned_lookup_forwards_both_ids_and_hides_absence() -> None:
    session = MagicMock(spec=Session)
    user_id = uuid4()
    document = _document(user_id)
    found_repository = ControlledRepository(document)
    result = get_owned_knowledge_document(
        document.id,
        user_id,
        session,
        repository_factory=repository_factory(found_repository),
    )
    assert result.id == document.id
    assert found_repository.lookup == (document.id, user_id)
    with pytest.raises(KnowledgeDocumentNotFoundError):
        get_owned_knowledge_document(
            document.id,
            uuid4(),
            session,
            repository_factory=repository_factory(ControlledRepository(None)),
        )
