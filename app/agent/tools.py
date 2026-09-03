"""Strict allowlisted Agent tools over the Domain Service gateway."""

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from app.agent.context import AgentRuntimeContext
from app.agent.providers import ProviderToolDefinition
from app.schemas.project import ProjectListResponse
from app.schemas.task import (
    TASK_UPDATE_EMPTY_MESSAGE,
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)
from app.services.agent_domain import AgentDomainGateway

AGENT_TOOL_NOT_ALLOWED_MESSAGE = "Agent tool is not allowed"
AGENT_TOOL_INPUT_MESSAGE = "Agent tool input is invalid"


class AgentToolNotAllowedError(Exception):
    """Reject unknown or runtime-disabled capabilities without echoing input."""


class AgentToolInputError(ValueError):
    """Reject invalid model arguments without carrying their raw values."""


class ListProjectsToolArguments(BaseModel):
    """Expose only bounded owner-independent Project list controls."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    include_archived: bool = False


class ListTasksToolArguments(TaskListQuery):
    """Reuse the existing bounded Task query contract without identity input."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class CreateTaskToolArguments(TaskCreate):
    """Reuse public Task creation fields without exposing trusted ownership."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class UpdateTaskToolArguments(TaskUpdate):
    """Combine a Task locator with the existing strict public update fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    task_id: UUID

    @model_validator(mode="after")
    def require_update_field(self) -> UpdateTaskToolArguments:
        """Do not let the locator satisfy TaskUpdate's non-empty contract."""

        if not (self.model_fields_set - {"task_id"}):
            raise ValueError(TASK_UPDATE_EMPTY_MESSAGE)
        return self

    def to_task_update(self) -> TaskUpdate:
        """Remove the locator before crossing the existing Service boundary."""

        values = {
            field_name: getattr(self, field_name)
            for field_name in TaskUpdate.model_fields
            if field_name in self.model_fields_set
        }
        return TaskUpdate.model_validate(values)


type AgentToolResult = ProjectListResponse | TaskListResponse | PublicTask


class AgentToolGateway(Protocol):
    """Keep tool dispatch independent from persistence implementation details."""

    def list_projects(
        self,
        *,
        user_id: UUID,
        page: int,
        page_size: int,
        include_archived: bool,
    ) -> ProjectListResponse: ...

    def list_tasks(
        self,
        *,
        user_id: UUID,
        query: TaskListQuery,
    ) -> TaskListResponse: ...

    def create_task(
        self,
        *,
        user_id: UUID,
        task_input: TaskCreate,
    ) -> PublicTask: ...

    def update_task(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        task_update: TaskUpdate,
    ) -> PublicTask: ...


READ_TOOL_NAMES = ("list_projects", "list_tasks")

READ_TOOL_DEFINITIONS = (
    ProviderToolDefinition(
        name="list_projects",
        description="List the authenticated user's projects with bounded pagination.",
        input_schema=ListProjectsToolArguments.model_json_schema(),
    ),
    ProviderToolDefinition(
        name="list_tasks",
        description=(
            "List the authenticated user's tasks with bounded filters and sorting."
        ),
        input_schema=ListTasksToolArguments.model_json_schema(),
    ),
)

WRITE_TOOL_NAMES = ("create_task", "update_task")
TOOL_NAMES = READ_TOOL_NAMES + WRITE_TOOL_NAMES

WRITE_TOOL_DEFINITIONS = (
    ProviderToolDefinition(
        name="create_task",
        description="Create a task in one project owned by the authenticated user.",
        input_schema=CreateTaskToolArguments.model_json_schema(),
    ),
    ProviderToolDefinition(
        name="update_task",
        description="Update editable fields on a task owned by the authenticated user.",
        input_schema=UpdateTaskToolArguments.model_json_schema(),
    ),
)

TOOL_DEFINITIONS = READ_TOOL_DEFINITIONS + WRITE_TOOL_DEFINITIONS


def available_tool_definitions(
    context: AgentRuntimeContext,
) -> tuple[ProviderToolDefinition, ...]:
    """Return only capabilities granted by trusted runtime policy."""

    if context.write_tools_enabled:
        return TOOL_DEFINITIONS
    return READ_TOOL_DEFINITIONS


def execute_tool(
    name: str,
    arguments: Mapping[str, object],
    context: AgentRuntimeContext,
    *,
    gateway: AgentToolGateway | None = None,
) -> AgentToolResult:
    """Validate one allowlisted request before opening any database Session."""

    if name not in TOOL_NAMES:
        raise AgentToolNotAllowedError(AGENT_TOOL_NOT_ALLOWED_MESSAGE)
    if name in WRITE_TOOL_NAMES and not context.write_tools_enabled:
        raise AgentToolNotAllowedError(AGENT_TOOL_NOT_ALLOWED_MESSAGE)

    validated: (
        ListProjectsToolArguments
        | ListTasksToolArguments
        | CreateTaskToolArguments
        | UpdateTaskToolArguments
    )
    try:
        if name == "list_projects":
            validated = ListProjectsToolArguments.model_validate(arguments)
        elif name == "list_tasks":
            validated = ListTasksToolArguments.model_validate(arguments)
        elif name == "create_task":
            validated = CreateTaskToolArguments.model_validate(arguments)
        else:
            validated = UpdateTaskToolArguments.model_validate(arguments)
    except ValidationError:
        raise AgentToolInputError(AGENT_TOOL_INPUT_MESSAGE) from None

    runtime_gateway = gateway if gateway is not None else AgentDomainGateway()
    if isinstance(validated, ListProjectsToolArguments):
        return runtime_gateway.list_projects(
            user_id=context.user_id,
            page=validated.page,
            page_size=validated.page_size,
            include_archived=validated.include_archived,
        )
    if isinstance(validated, ListTasksToolArguments):
        return runtime_gateway.list_tasks(user_id=context.user_id, query=validated)
    if isinstance(validated, CreateTaskToolArguments):
        return runtime_gateway.create_task(
            user_id=context.user_id,
            task_input=TaskCreate.model_validate(validated.model_dump()),
        )
    return runtime_gateway.update_task(
        user_id=context.user_id,
        task_id=validated.task_id,
        task_update=validated.to_task_update(),
    )
