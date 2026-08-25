# Product requirements

## Purpose and success criteria

The product is a learning task management backend built both for daily use and
as a job-search portfolio project. It must be runnable, tested, deployable, and
understandable by its owner.

The product succeeds when it provides a complete authenticated workflow for
projects and tasks, isolates every user's data, supplies trustworthy automated
tests, starts locally with Docker Compose, and documents the request path from
HTTP input to PostgreSQL and back.

## Users and core workflow

The MVP has one role: an authenticated user. A user can:

1. Register, sign in, refresh a session, sign out, view/update their profile,
   and change their password.
2. Create and organize learning projects.
3. Create, filter, complete, update, and delete tasks belonging to those
   projects.
4. Archive finished projects while retaining their tasks.

There is no administrator workflow in the MVP.

## MVP functional requirements

### Authentication and profile

- Normalize email addresses before enforcing uniqueness.
- Store passwords only as Argon2id hashes.
- Issue a short-lived JWT access token and a high-entropy refresh token.
- Store only refresh-token hashes. Rotate on refresh and reject reuse of the old
  token.
- Logout revokes the presented refresh token. A previously issued access token
  can remain valid until its short expiration unless a later version adds a
  denylist.
- Changing a password revokes all current refresh tokens for that user.
- Authentication failures use a generic message that does not reveal whether an
  account exists.
- API responses and logs never disclose passwords, password hashes, complete
  tokens, or secrets.

### Projects

- Create, retrieve, paginate, update, archive, and conditionally delete projects.
- Fields: `id`, `user_id`, `name`, `description`, `start_date`, `target_date`,
  `status`, `created_at`, and `updated_at`.
- Statuses: `NOT_STARTED`, `IN_PROGRESS`, `COMPLETED`, and `ARCHIVED`.
- `target_date` cannot precede `start_date`.
- A project containing tasks cannot be hard-deleted; return
  `409 PROJECT_NOT_EMPTY`. Archiving is the normal removal path.
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

## Explicitly outside the MVP

- Administrators, bans, account deletion, email verification, password reset,
  social login, recurring tasks, reminders, background jobs, Redis, rate-limit
  infrastructure, audit logs, AI recommendations, and cloud operations.
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
