"""Bounded hostile-file parsing and document transaction boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from uuid import UUID

from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.core.exceptions import (
    KNOWLEDGE_DOCUMENT_INVALID_MESSAGE,
    KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE,
    KNOWLEDGE_DOCUMENT_TOO_LARGE_MESSAGE,
    KnowledgeDocumentInvalidError,
    KnowledgeDocumentNotFoundError,
    KnowledgeDocumentTooLargeError,
)
from app.models.knowledge_document import KnowledgeDocument
from app.repositories.knowledge_documents import KnowledgeDocumentRepository
from app.schemas.knowledge_document import PublicKnowledgeDocument

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_PAGES = 100
MAX_DOCUMENT_TEXT_CHARACTERS = 200_000
MAX_DOCUMENT_NAME_CHARACTERS = 255
PDF_SIGNATURE = b"%PDF-"
DOCUMENT_PAGE_SEPARATOR = "\f"

MEDIA_TYPES_BY_EXTENSION = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
}

RepositoryFactory = Callable[[Session], KnowledgeDocumentRepository]


@dataclass(frozen=True, slots=True)
class ParsedKnowledgeDocument:
    """Carry validated private parsing output across the persistence boundary."""

    display_name: str
    media_type: str
    byte_count: int
    content_sha256: str
    page_count: int
    extracted_text: str


def _invalid() -> KnowledgeDocumentInvalidError:
    return KnowledgeDocumentInvalidError(KNOWLEDGE_DOCUMENT_INVALID_MESSAGE)


def _validate_persisted_text(value: str) -> str:
    """Reject PostgreSQL-incompatible NUL before Repository construction."""

    if "\x00" in value:
        raise _invalid()
    return value


def _display_name(filename: str | None) -> tuple[str, str]:
    if filename is None:
        raise _invalid()
    name = _validate_persisted_text(
        PurePosixPath(filename.replace("\\", "/")).name.strip()
    )
    if not name or len(name) > MAX_DOCUMENT_NAME_CHARACTERS:
        raise _invalid()
    extension = PurePosixPath(name).suffix.casefold()
    if extension not in MEDIA_TYPES_BY_EXTENSION:
        raise _invalid()
    return name, extension


def _normalize_text(value: str) -> str:
    normalized = _validate_persisted_text(
        value.replace("\r\n", "\n").replace("\r", "\n").strip()
    )
    if not normalized or len(normalized) > MAX_DOCUMENT_TEXT_CHARACTERS:
        raise _invalid()
    return normalized


def _normalize_pdf_page(value: str) -> str:
    """Normalize one page while retaining explicit boundaries between PDF pages."""

    return _validate_persisted_text(
        value.replace("\r\n", "\n").replace("\r", "\n").strip(" \t\n\v")
    )


def _parse_pdf(content: bytes) -> tuple[str, int]:
    if not content.startswith(PDF_SIGNATURE):
        raise _invalid()
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise _invalid()
        page_count = len(reader.pages)
        if not 1 <= page_count <= MAX_DOCUMENT_PAGES:
            raise _invalid()
        parts: list[str] = []
        character_count = 0
        for page in reader.pages:
            part = _normalize_pdf_page(page.extract_text() or "")
            character_count += len(part)
            if character_count > MAX_DOCUMENT_TEXT_CHARACTERS:
                raise _invalid()
            parts.append(part)
        extracted_text = DOCUMENT_PAGE_SEPARATOR.join(parts)
        if (
            not any(part for part in parts)
            or len(extracted_text) > MAX_DOCUMENT_TEXT_CHARACTERS
        ):
            raise _invalid()
        return extracted_text, page_count
    except KnowledgeDocumentInvalidError:
        raise
    except Exception:
        raise _invalid() from None


def parse_knowledge_document(
    *,
    filename: str | None,
    media_type: str | None,
    content: bytes,
) -> ParsedKnowledgeDocument:
    """Validate and parse one bounded local upload without network access."""

    if len(content) > MAX_DOCUMENT_BYTES:
        raise KnowledgeDocumentTooLargeError(KNOWLEDGE_DOCUMENT_TOO_LARGE_MESSAGE)
    if not content:
        raise _invalid()
    display_name, extension = _display_name(filename)
    expected_media_type = MEDIA_TYPES_BY_EXTENSION[extension]
    if media_type != expected_media_type:
        raise _invalid()

    if extension == ".pdf":
        extracted_text, page_count = _parse_pdf(content)
    else:
        if content.startswith(PDF_SIGNATURE):
            raise _invalid()
        try:
            extracted_text = _normalize_text(content.decode("utf-8-sig"))
        except UnicodeDecodeError:
            raise _invalid() from None
        page_count = 1

    return ParsedKnowledgeDocument(
        display_name=display_name,
        media_type=expected_media_type,
        byte_count=len(content),
        content_sha256=sha256(content).hexdigest(),
        page_count=page_count,
        extracted_text=_validate_persisted_text(extracted_text),
    )


def create_knowledge_document(
    *,
    filename: str | None,
    media_type: str | None,
    content: bytes,
    user_id: UUID,
    session: Session,
    repository_factory: RepositoryFactory = KnowledgeDocumentRepository,
) -> PublicKnowledgeDocument:
    """Parse and persist one document as an atomic owner-scoped use case."""

    parsed = parse_knowledge_document(
        filename=filename,
        media_type=media_type,
        content=content,
    )
    try:
        repository = repository_factory(session)
        document = repository.create(
            user_id=user_id,
            display_name=parsed.display_name,
            media_type=parsed.media_type,
            byte_count=parsed.byte_count,
            content_sha256=parsed.content_sha256,
            page_count=parsed.page_count,
            extracted_text=parsed.extracted_text,
        )
        result = PublicKnowledgeDocument.model_validate(document)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def get_owned_knowledge_document(
    document_id: UUID,
    user_id: UUID,
    session: Session,
    *,
    repository_factory: RepositoryFactory = KnowledgeDocumentRepository,
) -> PublicKnowledgeDocument:
    """Return public metadata without exposing private extracted text."""

    document: KnowledgeDocument | None = repository_factory(session).get_owned_by_id(
        document_id=document_id,
        user_id=user_id,
    )
    if document is None:
        raise KnowledgeDocumentNotFoundError(KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE)
    return PublicKnowledgeDocument.model_validate(document)
