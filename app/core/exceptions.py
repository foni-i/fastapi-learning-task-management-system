"""Safe domain exceptions shared across application boundaries."""

DUPLICATE_EMAIL_MESSAGE = "An account with this email already exists"
INVALID_CREDENTIALS_MESSAGE = "Invalid email or password"
AUTHENTICATION_REQUIRED_MESSAGE = "Could not validate credentials"
PROJECT_NOT_FOUND_MESSAGE = "Project does not exist"
TASK_NOT_FOUND_MESSAGE = "Task does not exist"
ARCHIVED_PROJECT_MESSAGE = "Archived project cannot be modified"


class DuplicateEmailError(Exception):
    """Signal a canonical email conflict without carrying persistence details."""


class InvalidCredentialsError(Exception):
    """Signal authentication failure without revealing which credential failed."""


class ProjectNotFoundError(Exception):
    """Hide whether a Project is absent or belongs to another user."""


class TaskNotFoundError(Exception):
    """Hide whether a Task is absent or belongs to another user."""


class ArchivedProjectError(Exception):
    """Signal that ordinary mutation cannot change an archived Project."""


class ProjectDateOrderError(ValueError):
    """Signal a safe Project date-order violation across persisted state."""
