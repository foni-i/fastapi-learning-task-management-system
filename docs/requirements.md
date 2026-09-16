# StudyFlow Agent product requirements

## Purpose and success criteria

The long-term product is **StudyFlow Agent: a personalized learning-planning and
task-management Agent built with FastAPI, LangGraph, and PostgreSQL**. It is a
backend-first learning project, not a generic chat bot. It must remain
runnable, tested, deployable, and understandable by its owner at every stage.

The product succeeds when it combines a complete authenticated project/task
workflow with a controlled Agent that plans and executes through the same domain
services. It must isolate every user's data, support structured output and tool
calling, resume approved workflows, stream progress, ground plans in cited user
material, and report repeatable evaluation evidence.

## Delivery tracks

- The **domain foundation** is authentication plus user-owned project and task
  operations. Ordinary HTTP APIs remain useful without any model provider.
- The **first Agent MVP** adds structured planning, service-backed tools, a
  single-agent LangGraph workflow, approval/resume, streaming, focused RAG, and
  evaluation. It does not require refresh-token/logout/password-change work.
- Refresh-token rotation, logout, and password change remain planned security
  hardening. They are deferred rather than deleted.
- MCP exposure and multi-agent orchestration are post-MVP enhancements.

## Users and core workflow

The MVP has one role: an authenticated user. A user can:

1. Register, sign in with a short-lived access token, and view/update their
   profile. Refresh, sign-out, and password-change hardening is a deferred track.
2. Create and organize learning projects.
3. Create, filter, complete, update, and delete tasks belonging to those
   projects.
4. Archive finished projects while retaining their tasks.

There is no administrator workflow in the MVP.

## MVP functional requirements

### Authentication and profile

- Normalize email addresses before enforcing uniqueness.
- Store passwords only as Argon2id hashes.
- Issue and strictly validate a short-lived JWT access token.
- A deferred authentication-hardening track issues high-entropy refresh tokens,
  stores only hashes, rotates them, supports logout revocation, and revokes all
  current refresh tokens after password change.
- Authentication failures use a generic message that does not reveal whether an
  account exists.
- API responses and logs never disclose passwords, password hashes, complete
  tokens, or secrets.

### Projects

- Create, retrieve, paginate, update, and archive projects.
- Fields: `id`, `user_id`, `name`, `description`, `start_date`, `target_date`,
  `status`, `created_at`, and `updated_at`.
- Statuses: `NOT_STARTED`, `IN_PROGRESS`, `COMPLETED`, and `ARCHIVED`.
- `target_date` cannot precede `start_date`.
- Archiving is the first Agent MVP removal path. Project hard deletion and its
  conflict contract are deferred domain enhancements.
- Every operation is scoped to the current user. Another user's project appears
  not to exist and returns 404.

### Tasks

- Create, retrieve, paginate, filter, sort, update, complete, and delete tasks.
- Fields: `id`, `user_id`, `project_id`, `title`, `description`, `status`,
  `priority`, `planned_date`, `due_at`, `estimated_minutes`, `completed_at`,
  `created_at`, and `updated_at`.
- Statuses: `TODO`, `IN_PROGRESS`, `COMPLETED`, and `CANCELLED`.
- Priorities: `LOW`, `MEDIUM`, `HIGH`, and `URGENT`.
- The project must belong to the current user. The service derives task
  `user_id`; clients cannot submit it.
- Completing a task sets `completed_at` on the server. Leaving `COMPLETED` clears
  it. Repeating the same status transition is idempotent.
- `estimated_minutes` is positive and receives a documented upper bound during
  task implementation.
- If both dates exist, `due_at` cannot be before the start of `planned_date`.
- A task is overdue when `due_at` is earlier than now and status is neither
  `COMPLETED` nor `CANCELLED`.
- Lists support `page`, `page_size`, filters for project/status/priority/date/
  overdue/title, an allowlist of sort fields, and a deterministic secondary sort.

### Agent MVP

- Model calls use strict Pydantic contracts, bounded timeouts/retries, prompt
  versions, and recorded latency/token usage.
- Agent tools initially expose `list_projects`, `list_tasks`, `create_task`, and
  `update_task`. Tools call domain services and receive identity only from a
  trusted authentication context; the model cannot supply or override `user_id`.
- The first workflow is one LangGraph Agent with explicit serializable state and
  nodes for analysis, context, planning, validation, approval, execution,
  verification, and summary. Deterministic code owns authorization and writes.
- PostgreSQL-backed checkpoints support interruption and restart recovery.
  Business run/approval summaries remain separate from checkpoint storage.
- High-impact operations require approval and idempotent execution. Hidden model
  reasoning is never persisted or exposed.
- SSE streams safe progress events. RAG is limited to user-provided syllabi,
  exam requirements, and study material, with source citations.
- A versioned 30–50 sample offline evaluation measures extraction, tool and
  argument accuracy, constraint violations, writes, approval bypass, recovery,
  duplicates, latency, and token cost.

### Platform behavior

- Version all product routes under `/api/v1`.
- Provide `/health/live` and a database-aware `/health/ready`.
- Use 201 for creation, 204 for successful deletion without a body, 401 for
  failed authentication, 403 for authenticated actions that are forbidden, 404
  for absent or hidden resources, 409 for business conflicts, and 422 for input
  validation failures.
- Paginated responses contain `items`, `page`, `page_size`, `total`, and `pages`.
- PATCH distinguishes a missing field from a field explicitly set to `null`.
- Public timestamps include an offset and are stored as UTC.
- Business and validation failures use the common envelope:

```json
{
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "Task does not exist",
    "details": null
  },
  "request_id": "01H..."
}
```

## Non-functional requirements

- Modular monolith; no microservices in the MVP.
- PostgreSQL in development, integration tests, and production-like execution.
- Explicit transaction ownership with rollback on failures.
- Pagination and bounded user input on all unbounded collections/strings.
- Request IDs and structured logs containing method, route template, status,
  duration, and request ID without sensitive data.
- OpenAPI excludes internal fields and can be disabled in production settings.
- Docker Compose provides repeatable local startup.
- CI runs pytest, Ruff lint, Ruff format check, and mypy.
- Tests cover success, failure, boundaries, and cross-user isolation. Core modules
  aim for useful coverage near 80%, without treating the number as a substitute
  for meaningful assertions.

## Explicitly outside the first Agent MVP

- Administrators, bans, account deletion, email verification, password reset,
  social login, recurring tasks, reminders, message queues, Redis, Kubernetes,
  generalized autonomous actions, and cloud operations.
- Multi-agent coordination and MCP exposure are post-MVP enhancements.
- Tags, study sessions, aggregated actual duration, and statistics are Version 2.
- Task `actual_minutes` is not client-editable; Version 2 derives actual time from
  study sessions.

## Version 2 summary

Version 2 adds user-owned tags, non-overlapping study sessions whose duration is
calculated by the server, task actual-duration aggregation, and bounded,
timezone-aware statistics. It begins only after the complete MVP is stable and
understood.

## Stage 0 assumptions and open decisions

- Use UUID primary keys consistently, generated with a PostgreSQL default. This
  provides opaque public identifiers at a modest index-size cost.
- Use Python 3.14, the current stable feature series at baseline time. Confirm
  dependency compatibility while locking packages in Stage 1.
- Use `uv` for environments and dependency locking, and mypy for type checking.
- Use SQLAlchemy 2 synchronous sessions as required by the governing brief.
- Store UTC and defer display-timezone decisions to clients. Version 2 will add a
  user/business timezone setting before statistics require local-day boundaries.
- No product or architecture decision requires owner input before Stage 1. Exact
  token lifetimes, password policy bounds, pagination bounds, and CORS origins are
  configuration details to define in the task that introduces each feature.
