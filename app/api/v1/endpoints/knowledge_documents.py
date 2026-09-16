"""Authenticated document ingestion and safe metadata endpoints."""

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.agent.embeddings import OpenAIEmbeddingProvider
from app.api.dependencies import get_current_user
from app.core.config import get_settings
from app.core.exceptions import (
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
from app.models.user import User
from app.schemas.auth import AuthenticationErrorResponse
from app.schemas.knowledge_document import (
    KnowledgeDocumentErrorResponse,
    KnowledgeDocumentIndexResponse,
    PublicKnowledgeDocument,
)
from app.services.knowledge_document_indexing import index_knowledge_document
from app.services.knowledge_documents import (
    MAX_DOCUMENT_BYTES,
    create_knowledge_document,
    get_owned_knowledge_document,
)

router = APIRouter(prefix="/knowledge/documents", tags=["knowledge"])

AUTHENTICATION_RESPONSE = {
    "model": AuthenticationErrorResponse,
    "description": "Bearer authentication required",
}


def _raise_invalid_document() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=KNOWLEDGE_DOCUMENT_INVALID_MESSAGE,
    )


def _raise_document_not_found() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=KNOWLEDGE_DOCUMENT_NOT_FOUND_MESSAGE,
    )


@router.post(
    "",
    response_model=PublicKnowledgeDocument,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_413_CONTENT_TOO_LARGE: {
            "model": KnowledgeDocumentErrorResponse,
            "description": "Document exceeds the accepted upload size",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": KnowledgeDocumentErrorResponse,
            "description": "Document is invalid or unsupported",
        },
    },
)
def upload_knowledge_document_endpoint(
    file: Annotated[UploadFile, File()],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PublicKnowledgeDocument:
    """Read at most one byte beyond the limit and always close the upload."""

    try:
        content = file.file.read(MAX_DOCUMENT_BYTES + 1)
        return create_knowledge_document(
            filename=file.filename,
            media_type=file.content_type,
            content=content,
            user_id=current_user.id,
            session=session,
        )
    except KnowledgeDocumentTooLargeError:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=KNOWLEDGE_DOCUMENT_TOO_LARGE_MESSAGE,
        ) from None
    except KnowledgeDocumentInvalidError:
        _raise_invalid_document()
    finally:
        file.file.close()


@router.get(
    "/{document_id}",
    response_model=PublicKnowledgeDocument,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: {
            "model": KnowledgeDocumentErrorResponse,
            "description": "Document absent or not owned by the current user",
        },
    },
)
def get_knowledge_document_endpoint(
    document_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PublicKnowledgeDocument:
    """Return only owner-scoped public metadata."""

    try:
        return get_owned_knowledge_document(document_id, current_user.id, session)
    except KnowledgeDocumentNotFoundError:
        _raise_document_not_found()


@router.post(
    "/{document_id}/index",
    response_model=KnowledgeDocumentIndexResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: {
            "model": KnowledgeDocumentErrorResponse,
            "description": "Document absent or not owned by the current user",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": KnowledgeDocumentErrorResponse,
            "description": "Embedding provider or indexing is unavailable",
        },
    },
)
def index_knowledge_document_endpoint(
    document_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> KnowledgeDocumentIndexResponse:
    """Explicitly index one owned parsed document without exposing private text."""

    settings = get_settings()
    if settings.model_provider != "openai":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=KNOWLEDGE_DOCUMENT_INDEXING_MESSAGE,
        )
    provider = OpenAIEmbeddingProvider(
        api_key=settings.model_api_key,
        model=settings.embedding_model,
    )
    try:
        return index_knowledge_document(
            document_id,
            current_user.id,
            session,
            embedding_provider=provider,
            timeout_seconds=settings.embedding_timeout_seconds,
        )
    except KnowledgeDocumentNotFoundError:
        _raise_document_not_found()
    except KnowledgeDocumentIndexingError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=KNOWLEDGE_DOCUMENT_INDEXING_MESSAGE,
        ) from None
