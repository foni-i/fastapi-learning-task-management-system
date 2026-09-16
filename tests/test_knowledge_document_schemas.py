"""Public document schema and ORM storage contract tests."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Table

from app.models.knowledge_document import KnowledgeDocument, KnowledgeDocumentStatus
from app.schemas.knowledge_document import PublicKnowledgeDocument

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


def test_public_document_uses_exact_metadata_allowlist_and_utc() -> None:
    source = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        display_name="syllabus.pdf",
        media_type="application/pdf",
        byte_count=500,
        content_sha256="a" * 64,
        page_count=2,
        extracted_text="private course material",
        status="PARSED",
        created_at=datetime(2026, 9, 6, 12, tzinfo=timezone(timedelta(hours=8))),
        updated_at=datetime(2026, 9, 6, 12, tzinfo=timezone(timedelta(hours=8))),
    )

    public = PublicKnowledgeDocument.model_validate(source)

    assert set(public.model_dump()) == PUBLIC_FIELDS
    assert public.created_at.tzinfo is UTC
    serialized = public.model_dump_json()
    assert "private course material" not in serialized
    assert "content_sha256" not in serialized
    assert "user_id" not in serialized


def test_public_document_rejects_naive_time() -> None:
    with pytest.raises(ValidationError):
        PublicKnowledgeDocument(
            id=uuid4(),
            display_name="notes.txt",
            media_type="text/plain",
            byte_count=1,
            page_count=1,
            status=KnowledgeDocumentStatus.PARSED,
            created_at=datetime(2026, 9, 6),
            updated_at=datetime.now(UTC),
        )


def test_model_declares_all_named_integrity_constraints() -> None:
    table = cast(Table, KnowledgeDocument.__table__)
    names = {constraint.name for constraint in table.constraints}
    indexes = {index.name for index in table.indexes}

    assert names == {
        "pk_knowledge_documents",
        "uq_knowledge_documents_id_user_id",
        "fk_knowledge_documents_user_id_users",
        "ck_knowledge_documents_display_name_not_blank",
        "ck_knowledge_documents_media_type",
        "ck_knowledge_documents_byte_count",
        "ck_knowledge_documents_page_count",
        "ck_knowledge_documents_extracted_text_length",
        "ck_knowledge_documents_content_sha256",
        "ck_knowledge_documents_status",
    }
    assert indexes == {
        "ix_knowledge_documents_user_id",
        "ix_knowledge_documents_user_status",
    }
    assert {status.value for status in KnowledgeDocumentStatus} == {
        "PARSED",
        "INDEXED",
    }
