"""Owner-scoped snapshots and standards-compliant SSE framing."""

from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import datetime
from typing import Never
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import AGENT_RUN_NOT_FOUND_MESSAGE, AgentRunNotFoundError
from app.models.agent_run import AgentApprovalStatus, AgentRunStatus
from app.models.agent_tool_execution import AgentToolExecutionStatus
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.agent_tool_executions import AgentToolExecutionRepository
from app.schemas.agent_events import (
    ApprovalRequiredPayload,
    HeartbeatPayload,
    MetricsPayload,
    NodeStatusPayload,
    PublicAgentEvent,
    PublicAgentEventPayload,
    RunStatusPayload,
    SafeErrorPayload,
    TerminalResultPayload,
    ToolResultPayload,
    ToolStartedPayload,
)
from app.schemas.agent_run import (
    PublicAgentApproval,
    PublicAgentRun,
)
from app.schemas.agent_tool_execution import PublicAgentToolExecution

AGENT_EVENT_CURSOR_MESSAGE = "Agent event cursor is unavailable"
MAX_PUBLIC_TOOL_EXECUTIONS = 50
SSE_MEDIA_TYPE = "text/event-stream"

RunRepositoryFactory = Callable[[Session], AgentRunRepository]
ExecutionRepositoryFactory = Callable[[Session], AgentToolExecutionRepository]


class AgentEventCursorError(ValueError):
    """Reject an invalid or stale cursor without echoing its value."""


@dataclass(frozen=True)
class _Draft:
    occurred_at: datetime
    order: int
    stable_key: str
    payload: PublicAgentEventPayload


TERMINAL_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.REJECTED,
        AgentRunStatus.PARTIAL_FAILURE,
        AgentRunStatus.FAILED,
    }
)


def _event(
    run_id: UUID,
    sequence: int,
    draft: _Draft,
) -> PublicAgentEvent:
    return PublicAgentEvent(
        event_id=f"{run_id}:{sequence:08d}",
        sequence=sequence,
        run_id=run_id,
        event_type=draft.payload.kind,
        occurred_at=draft.occurred_at,
        payload=draft.payload,
    )


def _execution_drafts(execution: PublicAgentToolExecution) -> list[_Draft]:
    drafts = [
        _Draft(
            occurred_at=execution.started_at,
            order=30,
            stable_key=f"{execution.id}:started",
            payload=ToolStartedPayload(tool_name=execution.tool_name),
        )
    ]
    if execution.status is AgentToolExecutionStatus.COMPLETED:
        assert execution.completed_at is not None
        assert execution.result_summary is not None
        drafts.append(
            _Draft(
                occurred_at=execution.completed_at,
                order=40,
                stable_key=f"{execution.id}:result",
                payload=ToolResultPayload(
                    tool_name=execution.tool_name,
                    status=execution.status,
                    summary=execution.result_summary,
                ),
            )
        )
    elif execution.status in {
        AgentToolExecutionStatus.FAILED,
        AgentToolExecutionStatus.UNKNOWN,
    }:
        assert execution.completed_at is not None
        assert execution.error_code is not None
        drafts.append(
            _Draft(
                occurred_at=execution.completed_at,
                order=40,
                stable_key=f"{execution.id}:error",
                payload=SafeErrorPayload(error_code=execution.error_code),
            )
        )
    return drafts


def build_public_agent_events(
    run: PublicAgentRun,
    approvals: tuple[PublicAgentApproval, ...],
    executions: tuple[PublicAgentToolExecution, ...],
) -> tuple[PublicAgentEvent, ...]:
    """Project retained product records into one deterministic public stream."""

    drafts = [
        _Draft(
            occurred_at=run.updated_at,
            order=10,
            stable_key="run-status",
            payload=RunStatusPayload(status=run.status),
        )
    ]
    if run.current_node is not None:
        drafts.append(
            _Draft(
                occurred_at=run.updated_at,
                order=20,
                stable_key="node-status",
                payload=NodeStatusPayload(node=run.current_node),
            )
        )
    for approval in approvals:
        if approval.decision is AgentApprovalStatus.PENDING:
            drafts.append(
                _Draft(
                    occurred_at=approval.created_at,
                    order=25,
                    stable_key=f"approval:{approval.id}",
                    payload=ApprovalRequiredPayload(
                        revision=approval.revision,
                        proposal_fingerprint=approval.proposal_fingerprint,
                    ),
                )
            )
    for execution in executions:
        drafts.extend(_execution_drafts(execution))
    drafts.append(
        _Draft(
            occurred_at=run.updated_at,
            order=80,
            stable_key="metrics",
            payload=MetricsPayload(metrics=run.metrics),
        )
    )
    if run.status in TERMINAL_RUN_STATUSES:
        drafts.append(
            _Draft(
                occurred_at=run.updated_at,
                order=90,
                stable_key="terminal",
                payload=TerminalResultPayload(
                    status=run.status,
                    summary=run.summary,
                ),
            )
        )
    else:
        drafts.append(
            _Draft(
                occurred_at=run.updated_at,
                order=90,
                stable_key="heartbeat",
                payload=HeartbeatPayload(),
            )
        )
    drafts.sort(key=lambda item: (item.occurred_at, item.order, item.stable_key))
    return tuple(
        _event(run.id, sequence, draft)
        for sequence, draft in enumerate(drafts, start=1)
    )


def get_owned_agent_events(
    run_id: UUID,
    user_id: UUID,
    session: Session,
    *,
    run_repository_factory: RunRepositoryFactory = AgentRunRepository,
    execution_repository_factory: ExecutionRepositoryFactory = (
        AgentToolExecutionRepository
    ),
) -> tuple[PublicAgentEvent, ...]:
    """Materialize an owner-scoped event snapshot during one short read."""

    run_repository = run_repository_factory(session)
    run = run_repository.get_owned_run(run_id=run_id, user_id=user_id)
    if run is None:
        raise AgentRunNotFoundError(AGENT_RUN_NOT_FOUND_MESSAGE)
    approvals = tuple(
        PublicAgentApproval.model_validate(item)
        for item in run_repository.list_owned_approvals(
            run_id=run_id,
            user_id=user_id,
        )
    )
    executions = tuple(
        PublicAgentToolExecution.model_validate(item)
        for item in execution_repository_factory(session).list_owned_run_actions(
            run_id=run_id,
            user_id=user_id,
            limit=MAX_PUBLIC_TOOL_EXECUTIONS,
        )
    )
    return build_public_agent_events(
        PublicAgentRun.model_validate(run),
        approvals,
        executions,
    )


def _cursor_error() -> Never:
    raise AgentEventCursorError(AGENT_EVENT_CURSOR_MESSAGE)


def resume_after_event(
    events: tuple[PublicAgentEvent, ...],
    last_event_id: str | None,
) -> tuple[PublicAgentEvent, ...]:
    """Return only retained events after one exact opaque cursor."""

    if last_event_id is None:
        return events
    if not events:
        _cursor_error()
    run_id = events[0].run_id
    prefix = f"{run_id}:"
    if not last_event_id.startswith(prefix):
        _cursor_error()
    for index, event in enumerate(events):
        if event.event_id == last_event_id:
            return events[index + 1 :]
    _cursor_error()


def iter_sse_events(
    events: tuple[PublicAgentEvent, ...],
    *,
    on_close: Callable[[], None] | None = None,
) -> Generator[str]:
    """Frame a materialized snapshot and release generator resources on close."""

    try:
        for event in events:
            yield (
                f"id: {event.event_id}\n"
                f"event: {event.event_type.value}\n"
                f"data: {event.model_dump_json()}\n\n"
            )
    finally:
        if on_close is not None:
            on_close()
