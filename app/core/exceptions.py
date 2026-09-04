"""Safe domain exceptions shared across application boundaries."""

DUPLICATE_EMAIL_MESSAGE = "An account with this email already exists"
INVALID_CREDENTIALS_MESSAGE = "Invalid email or password"
AUTHENTICATION_REQUIRED_MESSAGE = "Could not validate credentials"
PROJECT_NOT_FOUND_MESSAGE = "Project does not exist"
TASK_NOT_FOUND_MESSAGE = "Task does not exist"
TASK_TRANSITION_MESSAGE = "Task status transition is not allowed"
ARCHIVED_PROJECT_MESSAGE = "Archived project cannot be modified"
AGENT_PLANNING_CONFIGURATION_MESSAGE = "Study planning is not configured"
AGENT_PLANNING_UNAVAILABLE_MESSAGE = "Study planning is temporarily unavailable"
AGENT_THREAD_NOT_FOUND_MESSAGE = "Agent thread does not exist"
AGENT_RUN_NOT_FOUND_MESSAGE = "Agent run does not exist"


class DuplicateEmailError(Exception):
    """Signal a canonical email conflict without carrying persistence details."""


class InvalidCredentialsError(Exception):
    """Signal authentication failure without revealing which credential failed."""


class ProjectNotFoundError(Exception):
    """Hide whether a Project is absent or belongs to another user."""


class TaskNotFoundError(Exception):
    """Hide whether a Task is absent or belongs to another user."""


class TaskDateOrderError(ValueError):
    """Signal an invalid effective Task planned/due date combination."""


class TaskTransitionError(ValueError):
    """Signal a disallowed Task lifecycle transition without internal details."""


class ArchivedProjectError(Exception):
    """Signal that ordinary mutation cannot change an archived Project."""


class ProjectDateOrderError(ValueError):
    """Signal a safe Project date-order violation across persisted state."""


class AgentPlanningConfigurationError(Exception):
    """Signal unavailable provider configuration without exposing credentials."""


class AgentPlanningUnavailableError(Exception):
    """Signal a bounded provider failure without exposing model payloads."""


class AgentThreadNotFoundError(Exception):
    """Hide whether an Agent thread is absent or belongs to another user."""


class AgentRunNotFoundError(Exception):
    """Hide whether an Agent run is absent or belongs to another user."""
