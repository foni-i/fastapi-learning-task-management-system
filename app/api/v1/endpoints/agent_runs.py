"""Authenticated HTTP boundary for durable Agent runs and approvals."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.exceptions import (
    AGENT_RUN_CONFLICT_MESSAGE,
    AGENT_RUN_NOT_FOUND_MESSAGE,
    AGENT_WORKFLOW_UNAVAILABLE_MESSAGE,
    AgentRunConflictError,
    AgentRunNotFoundError,
    AgentWorkflowUnavailableError,
)
from app.db.session import get_session
from app.models.user import User
from app.schemas.agent_run import (
    AgentApprovalPreview,
    AgentApprovalSubmission,
    AgentRunErrorResponse,
    AgentRunSnapshot,
    AgentRunStartRequest,
)
from app.schemas.auth import AuthenticationErrorResponse
from app.services.agent_events import (
    AGENT_EVENT_CURSOR_MESSAGE,
    SSE_MEDIA_TYPE,
    AgentEventCursorError,
    get_owned_agent_events,
    iter_sse_events,
    resume_after_event,
)
from app.services.agent_workflow import (
    get_agent_approval_preview,
    get_agent_run_snapshot,
    start_agent_run,
    submit_agent_approval,
)

router = APIRouter(prefix="/agent", tags=["agent"])

AUTHENTICATION_RESPONSE = {
    "model": AuthenticationErrorResponse,
    "description": "Bearer authentication required",
}
NOT_FOUND_RESPONSE = {
    "model": AgentRunErrorResponse,
    "description": "Agent run absent or not owned by the current user",
}
CONFLICT_RESPONSE = {
    "model": AgentRunErrorResponse,
    "description": "Approval does not match the current or persisted decision",
}
UNAVAILABLE_RESPONSE = {
    "model": AgentRunErrorResponse,
    "description": "Durable Agent workflow infrastructure unavailable",
}


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=AGENT_RUN_NOT_FOUND_MESSAGE)


def _conflict() -> HTTPException:
    return HTTPException(status_code=409, detail=AGENT_RUN_CONFLICT_MESSAGE)


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail=AGENT_WORKFLOW_UNAVAILABLE_MESSAGE)


@router.post(
    "/runs",
    response_model=AgentRunSnapshot,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_503_SERVICE_UNAVAILABLE: UNAVAILABLE_RESPONSE,
    },
)
def start_run(
    request: AgentRunStartRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> AgentRunSnapshot:
    try:
        return start_agent_run(request, current_user.id, session)
    except AgentWorkflowUnavailableError:
        raise _unavailable() from None


@router.get(
    "/runs/{run_id}",
    response_model=AgentRunSnapshot,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: NOT_FOUND_RESPONSE,
    },
)
def read_run(
    run_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> AgentRunSnapshot:
    try:
        return get_agent_run_snapshot(run_id, current_user.id, session)
    except AgentRunNotFoundError:
        raise _not_found() from None


@router.get(
    "/runs/{run_id}/approval-preview",
    response_model=AgentApprovalPreview,
    response_model_exclude_unset=True,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: NOT_FOUND_RESPONSE,
        status.HTTP_409_CONFLICT: CONFLICT_RESPONSE,
        status.HTTP_503_SERVICE_UNAVAILABLE: UNAVAILABLE_RESPONSE,
    },
)
def read_approval_preview(
    run_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> AgentApprovalPreview:
    try:
        return get_agent_approval_preview(run_id, current_user.id, session)
    except AgentRunNotFoundError:
        raise _not_found() from None
    except AgentRunConflictError:
        raise _conflict() from None
    except AgentWorkflowUnavailableError:
        raise _unavailable() from None


@router.get(
    "/runs/{run_id}/events",
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {
            "description": "Ordered public Agent events",
            "content": {SSE_MEDIA_TYPE: {"schema": {"type": "string"}}},
        },
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: NOT_FOUND_RESPONSE,
        status.HTTP_409_CONFLICT: {
            "model": AgentRunErrorResponse,
            "description": "Event cursor is invalid or no longer retained",
        },
    },
)
def stream_run_events(
    run_id: UUID,
    current_user: Annotated[
        User,
        Depends(get_current_user, scope="function"),
    ],
    session: Annotated[
        Session,
        Depends(get_session, scope="function"),
    ],
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID", max_length=128),
    ] = None,
) -> StreamingResponse:
    try:
        events = resume_after_event(
            get_owned_agent_events(run_id, current_user.id, session),
            last_event_id,
        )
    except AgentRunNotFoundError:
        raise _not_found() from None
    except AgentEventCursorError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=AGENT_EVENT_CURSOR_MESSAGE,
        ) from None
    return StreamingResponse(
        iter_sse_events(events),
        media_type=SSE_MEDIA_TYPE,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/runs/{run_id}/approval",
    response_model=AgentRunSnapshot,
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTHENTICATION_RESPONSE,
        status.HTTP_404_NOT_FOUND: NOT_FOUND_RESPONSE,
        status.HTTP_409_CONFLICT: CONFLICT_RESPONSE,
        status.HTTP_503_SERVICE_UNAVAILABLE: UNAVAILABLE_RESPONSE,
    },
)
def decide_run(
    run_id: UUID,
    submission: AgentApprovalSubmission,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> AgentRunSnapshot:
    try:
        return submit_agent_approval(run_id, submission, current_user.id, session)
    except AgentRunNotFoundError:
        raise _not_found() from None
    except AgentRunConflictError:
        raise _conflict() from None
    except AgentWorkflowUnavailableError:
        raise _unavailable() from None
