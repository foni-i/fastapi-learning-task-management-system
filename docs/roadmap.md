# Delivery roadmap

## Working method

Complete one 1–2 hour task at a time. Each task starts by reading the governing
documents and inspecting current changes, and ends with relevant tests plus a
diff review. A stage ends only after its acceptance criteria pass; then work
stops for owner confirmation.

## Baseline discovered in Stage 0

- The repository directory contains only
  `CODEX_FASTAPI_LEARNING_TASK_SYSTEM.md` before Stage 0 documentation.
- The directory is not yet a Git repository.
- Git 2.45.1 is available.
- Python, the Python launcher, `uv`, and Docker are not on `PATH`.
- Anaconda Python 3.11.5 is callable by absolute path and its `pip` is on `PATH`,
  but it is below the planned Python 3.14 baseline.
- Therefore no application, dependency, test, lint, type, container, or migration
  command can honestly be reported as passing yet.

These are setup gaps, not conflicts with the product design. Stage 1 begins by
establishing a reproducible Python toolchain. Git initialization requires an
explicit Stage 1 task; Docker is required no later than Stage 2.

## Stage 1: runnable skeleton

### Task 1.1 — Repository and Python project baseline

**Goal:** Establish version control and a reproducible Python 3.14 project with
locked development tooling, without adding business features.

**Files:** `.gitignore`, `.python-version`, `pyproject.toml`, `uv.lock`,
`.env.example`, and possibly repository metadata created by `git init`.

**Learning points:** virtual environments, dependency groups, version constraints,
lock files, and the difference between interpreter and package manager.

**Acceptance:**

- `git status` works and secret/local files are ignored.
- `uv python install 3.14` (if needed), `uv sync --all-groups`, and
  `uv run python --version` succeed with Python 3.14.x.
- Stable compatible versions of FastAPI/Pydantic and quality tools are locked.
- No `app/` business module or database code is introduced.

### Task 1.2 — Minimal application and settings

**Goal:** Create the smallest importable FastAPI application and typed settings,
without database, authentication, or feature routes.

**Files:** `app/__init__.py`, `app/main.py`, `app/core/__init__.py`,
`app/core/config.py`, and focused tests.

**Learning points:** Python packages, imports, application instances, settings
loading, environment separation, and import-time side effects.

**Acceptance:**

- `uv run fastapi dev app/main.py` starts the app.
- A focused test imports the app and proves basic metadata/configuration.
- Missing optional local `.env` does not break safe development/test defaults.
- No database or business endpoint is created.

### Task 1.3 — API routing skeleton and liveness endpoint

**Goal:** Introduce the versioned router boundary and `/health/live` only.

**Files:** `app/api/__init__.py`, `app/api/v1/__init__.py`,
`app/api/v1/router.py`, `app/api/v1/endpoints/__init__.py`, a health endpoint
module, `app/main.py`, and API tests.

**Learning points:** APIRouter composition, URL prefixes, response models, status
codes, and FastAPI's test client.

**Acceptance:**

- `GET /health/live` returns 200 with a stable documented response.
- Unknown paths return 404.
- The `/api/v1` router is mounted but contains no product functionality.
- Focused success and method/path failure tests pass.

### Task 1.4 — Test and quality configuration

**Goal:** Make pytest, Ruff, and mypy first-class reproducible quality gates.

**Files:** `pyproject.toml`, `tests/__init__.py`, `tests/conftest.py`, existing
tests, and minimal configuration exclusions if justified.

**Learning points:** test discovery, fixtures, lint versus formatting, static
types, and why quality commands must run in the locked environment.

**Acceptance:**

- `uv run pytest` passes.
- `uv run ruff check .` passes.
- `uv run ruff format --check .` passes.
- `uv run mypy app tests` passes without blanket ignores.

### Task 1.5 — Stage 1 usage documentation and final verification

**Goal:** Document a from-scratch local workflow and verify the complete Stage 1
skeleton.

**Files:** `README.md` and corrections to existing Stage 1 files only.

**Learning points:** executable documentation, development server lifecycle,
environment variables, and interpreting quality-gate failures.

**Acceptance:**

- README prerequisites and commands work from a clean checkout with `uv`.
- README documents `/health/live`, current limitations, and the next stage.
- All four Stage 1 quality commands pass with recorded real output.
- Diff review finds no secrets, database implementation, authentication, or
  other Stage 2+ work.
- Stop and request owner confirmation before Stage 2.

## Later stages

### Stage 2 — PostgreSQL and migrations

Add synchronous Session/Base, Docker Compose PostgreSQL, Alembic, a dedicated
PostgreSQL test database, and database-aware readiness. Prove empty-database
upgrade, one downgrade, re-upgrade, and readiness behavior.

### Stage 3 — Registration

Add the user model and migration, normalized unique email, Argon2id hashing,
register schema/repository/service/router, and success/conflict/validation/
non-disclosure tests.

### Stage 4 — Login and current user

Add access JWT issuance and validation, authentication dependency, login, and
current-user read/update. Test valid, invalid, expired, and tampered tokens.

### Stage 5 — Refresh, logout, and password change

Add hashed refresh-token persistence, atomic rotation and revocation, logout,
and password change with global refresh-token revocation.

### Stage 6 — Projects

Implement in separate tasks: create, detail, paginated list, update, archive, and
conditional delete. Test date rules, ownership isolation, pagination, and 409 for
non-empty deletion.

### Stage 7 — Tasks

Implement in separate tasks: create, detail, paginated list, filtering/sorting,
update, state transitions, and delete. Test project ownership, dates, completion
timestamps, idempotency, stable sorting, and isolation.

### Stage 8 — Engineering completion

Finish common error translation, request IDs, structured logs, application Docker
service, CI, complete README/API examples, and clean-environment verification.

### Stage 9 — Version 2

Only after MVP acceptance, implement tags, study sessions, actual-duration
aggregation, and statistics as individually tested tasks.

## Stage 0 consistency checklist

- MVP and Version 2 boundaries match `docs/requirements.md` and the governing
  brief.
- Layering, transactions, ownership, and technology choices match
  `docs/architecture.md` and `AGENTS.md`.
- Stage 1 tasks contain no database, authentication, project, or task behavior.
- Environment gaps are recorded without claiming unavailable checks passed.
- There are no unresolved decisions that block Task 1.1.
