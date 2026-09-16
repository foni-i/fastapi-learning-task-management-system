"""Connection-free HTTP tests for owner-scoped document ingestion."""

from collections.abc import Iterator
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from app.api.dependencies import get_current_user
from app.api.v1.endpoints import knowledge_documents
from app.core.exceptions import (
    AUTHENTICATION_REQUIRED_MESSAGE,
    KNOWLEDGE_DOCUMENT_INDEXING_MESSAGE,
    KNOWLEDGE_DOCUMENT_INVALID_MESSAGE,
    KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE,
    KNOWLEDGE_DOCUMENT_TOO_LARGE_MESSAGE,
    KnowledgeDocumentIndexingError,
    KnowledgeDocumentInvalidError,
    KnowledgeDocumentNotFoundError,
    KnowledgeDocumentTooLargeError,
)
from app.db.session import get_session
from app.main import app
from app.models import KnowledgeDocumentStatus, User
from app.schemas.knowledge_document import (
    KnowledgeDocumentIndexResponse,
    PublicKnowledgeDocument,
)
from app.services.knowledge_documents import MAX_DOCUMENT_BYTES

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


def _user() -> User:
    now = datetime.now(UTC)
    return cast(
        User,
        SimpleNamespace(
            id=uuid4(),
            email="document-owner@example.com",
            password_hash="internal-test-value",
            created_at=now,
            updated_at=now,
        ),
    )


def _public(
    document_id: UUID | None = None,
    *,
    document_status: KnowledgeDocumentStatus = KnowledgeDocumentStatus.PARSED,
) -> PublicKnowledgeDocument:
    now = datetime.now(UTC)
    return PublicKnowledgeDocument(
        id=document_id or uuid4(),
        display_name="notes.txt",
        media_type="text/plain",
        byte_count=5,
        page_count=1,
        status=document_status,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def document_request_context() -> Iterator[tuple[MagicMock, User, list[str]]]:
    session = MagicMock(spec=Session)
    user = _user()
    lifecycle: list[str] = []

    def override_session() -> Iterator[Session]:
        lifecycle.append("opened")
        try:
            yield session
        finally:
            lifecycle.append("closed")

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield session, user, lifecycle
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_upload_delegates_bounded_bytes_owner_and_session(
    client: TestClient,
    document_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, user, lifecycle = document_request_context
    observed: dict[str, object] = {}

    def fake_create(**values: object) -> PublicKnowledgeDocument:
        observed.update(values)
        return _public()

    monkeypatch.setattr(
        knowledge_documents,
        "create_knowledge_document",
        fake_create,
    )
    response = client.post(
        PATH,
        files={"file": ("notes.txt", b"notes", "text/plain")},
    )

    assert response.status_code == 201
    assert set(response.json()) == PUBLIC_FIELDS
    assert observed == {
        "filename": "notes.txt",
        "media_type": "text/plain",
        "content": b"notes",
        "user_id": user.id,
        "session": session,
    }
    assert lifecycle == ["opened", "closed"]


def test_upload_always_closes_the_input_file(monkeypatch: pytest.MonkeyPatch) -> None:
    upload = UploadFile(
        file=BytesIO(b"notes"),
        filename="notes.txt",
        headers=Headers({"content-type": "text/plain"}),
    )
    monkeypatch.setattr(
        knowledge_documents,
        "create_knowledge_document",
        lambda **_values: _public(),
    )

    result = knowledge_documents.upload_knowledge_document_endpoint(
        file=upload,
        session=MagicMock(spec=Session),
        current_user=_user(),
    )

    assert result.display_name == "notes.txt"
    assert upload.file.closed


def test_upload_maps_safe_validation_and_size_errors(
    client: TestClient,
    document_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid(**_values: object) -> PublicKnowledgeDocument:
        raise KnowledgeDocumentInvalidError(KNOWLEDGE_DOCUMENT_INVALID_MESSAGE)

    monkeypatch.setattr(knowledge_documents, "create_knowledge_document", invalid)
    invalid_response = client.post(
        PATH,
        files={"file": ("private-name.txt", b"private-content", "text/plain")},
    )
    assert invalid_response.status_code == 422
    assert invalid_response.json() == {"detail": KNOWLEDGE_DOCUMENT_INVALID_MESSAGE}
    assert "private-name" not in invalid_response.text
    assert "private-content" not in invalid_response.text

    def too_large(**_values: object) -> PublicKnowledgeDocument:
        raise KnowledgeDocumentTooLargeError(KNOWLEDGE_DOCUMENT_TOO_LARGE_MESSAGE)

    monkeypatch.setattr(
        knowledge_documents,
        "create_knowledge_document",
        too_large,
    )
    size_response = client.post(
        PATH,
        files={
            "file": (
                "large.txt",
                b"x" * (MAX_DOCUMENT_BYTES + 1),
                "text/plain",
            )
        },
    )
    assert size_response.status_code == 413


def test_upload_accepts_exact_file_limit_and_rejects_one_more_byte(
    client: TestClient,
    document_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_sizes: list[int] = []

    def accept_exact(**values: object) -> PublicKnowledgeDocument:
        content = cast(bytes, values["content"])
        observed_sizes.append(len(content))
        return _public()

    monkeypatch.setattr(knowledge_documents, "create_knowledge_document", accept_exact)
    exact = client.post(
        PATH,
        files={"file": ("large.txt", b"x" * MAX_DOCUMENT_BYTES, "text/plain")},
    )
    assert exact.status_code == 201
    assert observed_sizes == [MAX_DOCUMENT_BYTES]

    def reject_larger(**values: object) -> PublicKnowledgeDocument:
        content = cast(bytes, values["content"])
        observed_sizes.append(len(content))
        raise KnowledgeDocumentTooLargeError(KNOWLEDGE_DOCUMENT_TOO_LARGE_MESSAGE)

    monkeypatch.setattr(knowledge_documents, "create_knowledge_document", reject_larger)
    larger = client.post(
        PATH,
        files={
            "file": (
                "larger.txt",
                b"x" * (MAX_DOCUMENT_BYTES + 1),
                "text/plain",
            )
        },
    )

    assert larger.status_code == 413
    assert larger.json() == {"detail": KNOWLEDGE_DOCUMENT_TOO_LARGE_MESSAGE}
    assert observed_sizes == [MAX_DOCUMENT_BYTES, MAX_DOCUMENT_BYTES + 1]


def test_upload_maps_nul_to_safe_422_before_persistence(
    client: TestClient,
    document_request_context: tuple[MagicMock, User, list[str]],
) -> None:
    session, _user_value, lifecycle = document_request_context

    response = client.post(
        PATH,
        files={"file": ("unsafe.txt", b"private\x00text", "text/plain")},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": KNOWLEDGE_DOCUMENT_INVALID_MESSAGE}
    assert "private" not in response.text
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    assert lifecycle == ["opened", "closed"]


def test_detail_delegates_owner_and_hides_foreign_or_missing(
    client: TestClient,
    document_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, user, lifecycle = document_request_context
    document_id = uuid4()
    observed: list[tuple[UUID, UUID, Session]] = []

    def found(
        received_id: UUID,
        user_id: UUID,
        received_session: Session,
    ) -> PublicKnowledgeDocument:
        observed.append((received_id, user_id, received_session))
        return _public(received_id)

    monkeypatch.setattr(
        knowledge_documents,
        "get_owned_knowledge_document",
        found,
    )
    response = client.get(f"{PATH}/{document_id}")
    assert response.status_code == 200
    assert set(response.json()) == PUBLIC_FIELDS
    assert observed == [(document_id, user.id, session)]

    def missing(*_args: object) -> PublicKnowledgeDocument:
        raise KnowledgeDocumentNotFoundError(KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE)

    monkeypatch.setattr(
        knowledge_documents,
        "get_owned_knowledge_document",
        missing,
    )
    hidden = client.get(f"{PATH}/{uuid4()}")
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE}
    assert lifecycle == ["opened", "closed", "opened", "closed"]


def test_document_routes_require_bearer_authentication(client: TestClient) -> None:
    session = MagicMock(spec=Session)

    def override_session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        responses = (
            client.post(
                PATH,
                files={"file": ("notes.txt", b"notes", "text/plain")},
            ),
            client.get(f"{PATH}/{uuid4()}"),
            client.post(f"{PATH}/{uuid4()}/index"),
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    for response in responses:
        assert response.status_code == 401
        assert response.json() == {"detail": AUTHENTICATION_REQUIRED_MESSAGE}
        assert response.headers["www-authenticate"] == "Bearer"


def test_index_route_builds_configured_adapter_and_delegates_trusted_owner(
    client: TestClient,
    document_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, user, lifecycle = document_request_context
    document_id = uuid4()
    provider = object()
    observed: dict[str, object] = {}
    settings = SimpleNamespace(
        model_provider="openai",
        model_api_key=SecretStr("synthetic-key"),
        embedding_model="synthetic-embedding-model",
        embedding_timeout_seconds=6.0,
    )

    monkeypatch.setattr(knowledge_documents, "get_settings", lambda: settings)

    def provider_factory(**values: object) -> object:
        observed["provider_config"] = values
        return provider

    monkeypatch.setattr(
        knowledge_documents,
        "OpenAIEmbeddingProvider",
        provider_factory,
    )

    def fake_index(
        received_id: UUID,
        user_id: UUID,
        received_session: Session,
        **values: object,
    ) -> KnowledgeDocumentIndexResponse:
        observed["index"] = (
            received_id,
            user_id,
            received_session,
            values,
        )
        return KnowledgeDocumentIndexResponse(
            document=_public(
                received_id,
                document_status=KnowledgeDocumentStatus.INDEXED,
            ),
            chunk_count=2,
        )

    monkeypatch.setattr(knowledge_documents, "index_knowledge_document", fake_index)

    response = client.post(f"{PATH}/{document_id}/index")

    assert response.status_code == 200
    assert response.json()["chunk_count"] == 2
    assert response.json()["document"]["status"] == "INDEXED"
    assert observed["provider_config"] == {
        "api_key": settings.model_api_key,
        "model": settings.embedding_model,
    }
    assert observed["index"] == (
        document_id,
        user.id,
        session,
        {"embedding_provider": provider, "timeout_seconds": 6.0},
    )
    assert lifecycle == ["opened", "closed"]


@pytest.mark.parametrize(
    ("error", "expected_status", "message"),
    [
        (
            KnowledgeDocumentNotFoundError(KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE),
            404,
            KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE,
        ),
        (
            KnowledgeDocumentIndexingError(KNOWLEDGE_DOCUMENT_INDEXING_MESSAGE),
            503,
            KNOWLEDGE_DOCUMENT_INDEXING_MESSAGE,
        ),
    ],
)
def test_index_route_maps_safe_errors(
    client: TestClient,
    document_request_context: tuple[MagicMock, User, list[str]],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_status: int,
    message: str,
) -> None:
    settings = SimpleNamespace(
        model_provider="openai",
        model_api_key=SecretStr("synthetic-key"),
        embedding_model="synthetic-embedding-model",
        embedding_timeout_seconds=6.0,
    )
    monkeypatch.setattr(knowledge_documents, "get_settings", lambda: settings)
    monkeypatch.setattr(
        knowledge_documents,
        "OpenAIEmbeddingProvider",
        lambda **_values: object(),
    )

    def fail(*_args: object, **_kwargs: object) -> KnowledgeDocumentIndexResponse:
        raise error

    monkeypatch.setattr(knowledge_documents, "index_knowledge_document", fail)

    response = client.post(f"{PATH}/{uuid4()}/index")

    assert response.status_code == expected_status
    assert response.json() == {"detail": message}
