"""Real PostgreSQL pgvector migration and atomic document indexing proof."""

from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.agent.embeddings import EMBEDDING_DIMENSIONS
from app.core.config import get_settings
from app.core.exceptions import (
    KnowledgeDocumentIndexingError,
    KnowledgeDocumentNotFoundError,
)
from app.models import KnowledgeDocument, KnowledgeDocumentChunk, User
from app.repositories.knowledge_document_chunks import (
    KnowledgeDocumentChunkCreate,
    KnowledgeDocumentChunkRepository,
)
from app.services.knowledge_document_indexing import index_knowledge_document
from tests.fakes.embedding_provider import DeterministicEmbeddingProvider
from tests.integration.conftest import validate_migration_test_target

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT_REVISION = "d7a1e4c9b320"
CHUNK_REVISION = "e3b7c2d9a410"


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _index_definitions(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' "
                "AND tablename = 'knowledge_document_chunks'"
            )
        )
        return {str(name): str(definition) for name, definition in rows}


def test_pgvector_chunk_migration_downgrade_retains_documents(
    migration_test_database_url: URL,
    integration_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = migration_test_database_url.render_as_string(hide_password=False)
    configuration = Config(str(ROOT / "alembic.ini"))
    monkeypatch.setenv("STMS_DATABASE_URL", target)
    get_settings.cache_clear()
    user_id: UUID | None = None
    document_id: UUID | None = None

    try:
        validate_migration_test_target(target)
        command.downgrade(configuration, DOCUMENT_REVISION)
        assert _current_revision(integration_engine) == DOCUMENT_REVISION
        assert "knowledge_documents" in inspect(integration_engine).get_table_names()
        assert (
            "knowledge_document_chunks"
            not in inspect(integration_engine).get_table_names()
        )

        command.upgrade(configuration, CHUNK_REVISION)
        assert _current_revision(integration_engine) == CHUNK_REVISION
        inspector = inspect(integration_engine)
        columns = {
            item["name"]: item
            for item in inspector.get_columns("knowledge_document_chunks")
        }
        assert set(columns) == {
            "id",
            "document_id",
            "user_id",
            "page_number",
            "ordinal",
            "content",
            "content_fingerprint",
            "embedding",
            "search_vector",
        }
        assert str(columns["embedding"]["type"]) == "VECTOR(1536)"
        assert columns["search_vector"]["computed"] is not None
        assert inspector.get_pk_constraint("knowledge_document_chunks")["name"] == (
            "pk_knowledge_document_chunks"
        )
        assert {
            item["name"]
            for item in inspector.get_foreign_keys("knowledge_document_chunks")
        } == {"fk_knowledge_document_chunks_document_owner"}
        assert {
            item["name"]
            for item in inspector.get_unique_constraints("knowledge_document_chunks")
        } == {"uq_knowledge_document_chunks_document_ordinal"}
        assert {
            item["name"]
            for item in inspector.get_check_constraints("knowledge_document_chunks")
        } == {
            "ck_knowledge_document_chunks_content_length",
            "ck_knowledge_document_chunks_fingerprint",
            "ck_knowledge_document_chunks_ordinal",
            "ck_knowledge_document_chunks_page_number",
        }
        definitions = _index_definitions(integration_engine)
        assert (
            "USING gin (search_vector)"
            in definitions["ix_knowledge_document_chunks_search_vector"]
        )
        assert (
            "USING hnsw (embedding vector_cosine_ops)"
            in definitions["ix_knowledge_document_chunks_embedding_hnsw"]
        )
        with integration_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
                is not None
            )

        with Session(integration_engine) as session:
            user = User(
                email=f"chunk-migration-{uuid4()}@example.com",
                password_hash="synthetic-test-hash",
            )
            session.add(user)
            session.flush()
            document = KnowledgeDocument(
                user_id=user.id,
                display_name="migration.txt",
                media_type="text/plain",
                byte_count=7,
                content_sha256="a" * 64,
                page_count=1,
                extracted_text="content",
                status="INDEXED",
            )
            session.add(document)
            session.commit()
            user_id = user.id
            document_id = document.id

        validate_migration_test_target(target)
        command.downgrade(configuration, DOCUMENT_REVISION)
        assert _current_revision(integration_engine) == DOCUMENT_REVISION
        assert "knowledge_documents" in inspect(integration_engine).get_table_names()
        assert (
            "knowledge_document_chunks"
            not in inspect(integration_engine).get_table_names()
        )
        with integration_engine.connect() as connection:
            retained_status = connection.scalar(
                text("SELECT status FROM knowledge_documents WHERE id = :id"),
                {"id": document_id},
            )
            extension_count = connection.scalar(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
            )
        assert retained_status == "PARSED"
        assert extension_count == 0

        command.upgrade(configuration, CHUNK_REVISION)
        assert _current_revision(integration_engine) == CHUNK_REVISION
    finally:
        command.upgrade(configuration, "head")
        if document_id is not None and user_id is not None:
            with Session(integration_engine) as session:
                session.execute(
                    delete(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
                )
                session.execute(delete(User).where(User.id == user_id))
                session.commit()
        get_settings.cache_clear()


def _create_document(session: Session, *, suffix: str) -> KnowledgeDocument:
    user = User(
        email=f"chunk-{suffix}-{uuid4()}@example.com",
        password_hash="synthetic-test-hash",
    )
    session.add(user)
    session.flush()
    document = KnowledgeDocument(
        user_id=user.id,
        display_name="notes.txt",
        media_type="text/plain",
        byte_count=2001,
        content_sha256="b" * 64,
        page_count=1,
        extracted_text="x" * 2001,
    )
    session.add(document)
    session.commit()
    return document


class FailingAfterReplaceRepository(KnowledgeDocumentChunkRepository):
    """Exercise rollback after DELETE and INSERT have both reached PostgreSQL."""

    def replace_owned(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
        chunks: Sequence[KnowledgeDocumentChunkCreate],
    ) -> list[KnowledgeDocumentChunk]:
        super().replace_owned(
            document_id=document_id,
            user_id=user_id,
            chunks=chunks,
        )
        raise RuntimeError("synthetic persistence failure")


def test_indexing_stores_vectors_and_replacement_rolls_back_atomically(
    integration_engine: Engine,
) -> None:
    with Session(integration_engine) as session:
        document = _create_document(session, suffix="atomic")
        document_id = document.id
        user_id = document.user_id

    try:
        with Session(integration_engine) as session:
            result = index_knowledge_document(
                document_id,
                user_id,
                session,
                embedding_provider=DeterministicEmbeddingProvider(),
                timeout_seconds=1,
            )
        assert result.chunk_count == 2
        assert result.document.status.value == "INDEXED"

        with Session(integration_engine) as session:
            stored = list(
                session.scalars(
                    select(KnowledgeDocumentChunk)
                    .where(
                        KnowledgeDocumentChunk.document_id == document_id,
                        KnowledgeDocumentChunk.user_id == user_id,
                    )
                    .order_by(KnowledgeDocumentChunk.ordinal)
                )
            )
            original_ids = [item.id for item in stored]
            assert [item.ordinal for item in stored] == [0, 1]
            assert [len(item.content) for item in stored] == [2000, 201]
            assert (
                session.scalar(
                    select(func.vector_dims(KnowledgeDocumentChunk.embedding)).limit(1)
                )
                == EMBEDDING_DIMENSIONS
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(KnowledgeDocumentChunk)
                    .where(KnowledgeDocumentChunk.search_vector.is_not(None))
                )
                == 2
            )

        with (
            Session(integration_engine) as session,
            pytest.raises(KnowledgeDocumentNotFoundError),
        ):
            index_knowledge_document(
                document_id,
                uuid4(),
                session,
                embedding_provider=DeterministicEmbeddingProvider(),
                timeout_seconds=1,
            )

        with (
            Session(integration_engine) as session,
            pytest.raises(RuntimeError, match="synthetic persistence failure"),
        ):
            index_knowledge_document(
                document_id,
                user_id,
                session,
                embedding_provider=DeterministicEmbeddingProvider(),
                timeout_seconds=1,
                chunk_repository_factory=FailingAfterReplaceRepository,
            )

        with Session(integration_engine) as session:
            retained_ids = list(
                session.scalars(
                    select(KnowledgeDocumentChunk.id)
                    .where(KnowledgeDocumentChunk.document_id == document_id)
                    .order_by(KnowledgeDocumentChunk.ordinal)
                )
            )
            assert retained_ids == original_ids
    finally:
        with Session(integration_engine) as session:
            session.execute(
                delete(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
            )
            session.execute(delete(User).where(User.id == user_id))
            session.commit()


def test_invalid_vectors_never_replace_existing_chunks(
    integration_engine: Engine,
) -> None:
    with Session(integration_engine) as session:
        document = _create_document(session, suffix="invalid-vector")
        document_id = document.id
        user_id = document.user_id

    class InvalidProvider:
        def embed(
            self,
            _texts: Sequence[str],
            *,
            timeout_seconds: float,
        ) -> Sequence[Sequence[float]]:
            assert timeout_seconds == 1
            return ([float("nan")] * EMBEDDING_DIMENSIONS,) * 2

    try:
        with (
            Session(integration_engine) as session,
            pytest.raises(KnowledgeDocumentIndexingError),
        ):
            index_knowledge_document(
                document_id,
                user_id,
                session,
                embedding_provider=InvalidProvider(),
                timeout_seconds=1,
            )
        with Session(integration_engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(KnowledgeDocumentChunk)
                    .where(KnowledgeDocumentChunk.document_id == document_id)
                )
                == 0
            )
    finally:
        with Session(integration_engine) as session:
            session.execute(
                delete(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
            )
            session.execute(delete(User).where(User.id == user_id))
            session.commit()


def test_postgresql_rejects_cross_owner_chunk_foreign_key(
    integration_engine: Engine,
) -> None:
    with Session(integration_engine) as session:
        document = _create_document(session, suffix="owner")
        document_id = document.id
        other = User(
            email=f"chunk-other-{uuid4()}@example.com",
            password_hash="synthetic-test-hash",
        )
        session.add(other)
        session.flush()
        session.add(
            KnowledgeDocumentChunk(
                document_id=document.id,
                user_id=other.id,
                page_number=1,
                ordinal=0,
                content="private chunk",
                content_fingerprint="c" * 64,
                embedding=[0.1] * EMBEDDING_DIMENSIONS,
            )
        )
        with pytest.raises(IntegrityError) as exc_info:
            session.flush()
        diagnostic = getattr(exc_info.value.orig, "diag", None)
        assert getattr(diagnostic, "constraint_name", None) == (
            "fk_knowledge_document_chunks_document_owner"
        )
        session.rollback()

    with Session(integration_engine) as session:
        owner_id = session.scalar(
            select(KnowledgeDocument.user_id).where(KnowledgeDocument.id == document_id)
        )
        session.execute(
            delete(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
        )
        if owner_id is not None:
            session.execute(delete(User).where(User.id == owner_id))
        session.commit()
