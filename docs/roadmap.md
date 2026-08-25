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

## Stage 2 — PostgreSQL and migrations

Stage 2 establishes database infrastructure only: SQLAlchemy 2 synchronous
sessions, PostgreSQL development and test instances, Alembic, migration
verification, and database-aware readiness. It must not introduce user,
authentication, project, task, or other product models. SQLite and asynchronous
SQLAlchemy are outside this stage. Each task below is time-boxed to approximately
1–2 hours.

### Environment gate and task order

The Stage 2 planning check found that `docker` is not currently on `PATH` in the
Windows environment. Tasks 2.1 through 2.3 can be completed and fully accepted
without Docker because they do not open a database connection. Before Task 2.4,
install and start Docker Desktop, then verify all of the following:

```powershell
docker --version
docker compose version
docker info
```

Tasks 2.4 through 2.8 require a running Docker engine because their acceptance
depends on real PostgreSQL behavior. The development and integration-test
databases must be separate PostgreSQL databases; tests must refuse SQLite and
must never target the development database. Complete the numbered tasks in
order, stopping after each one for owner review.

### Task 2.1 — SQLAlchemy dependencies and database URL settings

**Goal:** Lock SQLAlchemy 2 and the synchronous Psycopg PostgreSQL driver, then
add typed database URL configuration without creating an engine, Session,
database connection, model, or migration.

**Files:** `pyproject.toml`, `uv.lock`, `app/core/config.py`, `.env.example`, and
focused configuration tests.

**Learning points:** SQLAlchemy dialect-plus-driver URLs, runtime versus
development dependencies, Pydantic settings, environment-secret boundaries,
and why driver installation does not itself connect to PostgreSQL.

**Environment:** Docker Desktop is not required. Network access may be required
to resolve and download the new locked packages.

**Acceptance:**

- SQLAlchemy 2 and `psycopg` with its maintained binary installation option are
  normal runtime dependencies; async drivers and SQLite helpers are absent.
- `STMS_DATABASE_URL` loads through `Settings` and uses the
  `postgresql+psycopg` dialect/driver form.
- A missing URL remains an explicit unconfigured state so Stage 1 liveness and
  settings imports still work; database operations fail clearly rather than
  silently falling back to SQLite.
- `.env.example` contains only clearly marked local sample values; no real
  credential or `.env` file is committed.
- Tests prove environment override and invalid/non-PostgreSQL URL rejection
  without attempting a connection.
- No `app/db/`, Alembic files, model, business endpoint, or Docker file is added.

**Tests and verification:**

```powershell
uv sync --all-groups --locked
uv run pytest tests/test_config.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
uv lock --check
git diff --check
```

### Task 2.2 — Declarative Base and synchronous Session factory

**Goal:** Add the minimum SQLAlchemy infrastructure for an empty model metadata
registry, a synchronous engine, and short-lived synchronous Sessions, without
opening a connection at import time.

**Files:** `app/db/__init__.py`, `app/db/base.py`, `app/db/session.py`, and focused
unit tests such as `tests/test_db_session.py`.

**Learning points:** `DeclarativeBase`, Engine versus connection, `sessionmaker`,
one-Session-per-unit-of-work lifetime, lazy connection creation, cleanup, and
the rule that repositories never commit independently.

**Environment:** Docker Desktop is not required. Tests must inspect or substitute
the engine/session factory without connecting to a live database.

**Acceptance:**

- `Base` uses the SQLAlchemy 2 declarative API and has empty metadata; no product
  table or ORM model exists.
- Engine and `sessionmaker` use synchronous SQLAlchemy APIs and the configured
  PostgreSQL URL.
- Importing the application or database modules does not establish a network
  connection.
- A Session lifecycle helper yields one Session and always closes it; it does
  not hide commits or create repository/service abstractions prematurely.
- Focused tests prove configuration, synchronicity, lazy connection behavior,
  and cleanup using controlled substitutes rather than SQLite.

**Tests and verification:**

```powershell
uv run pytest tests/test_db_session.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
uv lock --check
git diff --check
```

### Task 2.3 — Alembic environment initialization

**Goal:** Install and initialize Alembic so it reads `STMS_DATABASE_URL` through
application settings and targets `Base.metadata`, but do not create a revision
or connect to PostgreSQL yet.

**Files:** `pyproject.toml`, `uv.lock`, `alembic.ini`, `alembic/env.py`,
`alembic/script.py.mako`, `alembic/versions/` only as needed to retain the empty
directory, and focused Alembic configuration tests.

**Learning points:** migration environment versus revision, online versus
offline migration context, `target_metadata`, configuration ownership, and why
database URLs must not be hard-coded into `alembic.ini`.

**Environment:** Docker Desktop is not required for this initialization task.
Only Alembic commands that do not contact a database are acceptance commands.

**Acceptance:**

- Alembic is locked as a project operational dependency and its CLI runs in the
  uv environment.
- `env.py` imports the empty `Base.metadata` and obtains the database URL from
  typed settings; `alembic.ini` contains no real credential.
- Both offline and future online configuration use the synchronous PostgreSQL
  path; no async template or adapter is present.
- No revision, schema object, application model, or live migration is created.
- Focused tests can load the migration configuration without opening a database
  connection.

**Tests and verification:**

```powershell
uv sync --all-groups --locked
$env:STMS_DATABASE_URL = "postgresql+psycopg://stms:stms@127.0.0.1:5432/stms"
uv run alembic --version
uv run alembic heads
uv run alembic history
uv run pytest tests/test_alembic_config.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

### Task 2.4 — Docker Compose PostgreSQL development and test environment

**Goal:** Provide repeatable, separately isolated PostgreSQL services for local
development and integration tests; do not containerize the FastAPI application
yet.

**Files:** `compose.yaml`, `.env.example`, `README.md`, and only minimal Docker
support files required by the two PostgreSQL services.

**Learning points:** Docker image pinning, service health checks, ports, named
development volumes, disposable test data, Compose environment interpolation,
and development/test database isolation.

**Environment:** Docker Desktop must be installed, running, and able to run Linux
containers before this task starts.

**Acceptance:**

- Compose defines `postgres-dev` at host port 5432 with local sample database
  `stms`, plus `postgres-test` at host port 5433 with local sample database
  `stms_test`; the services use distinct users and passwords.
- Both services have health checks; development data is persistent while test
  data can be recreated without deleting the development volume.
- Credentials are local samples supplied through environment configuration;
  Compose and README contain no real secret.
- `docker compose config` is valid, both services become healthy, and a simple
  PostgreSQL command succeeds independently against each database.
- No application image/service, ORM model, migration revision, SQLite service,
  authentication, or business code is added.

**Tests and verification:**

```powershell
docker --version
docker compose version
docker info
docker compose config
docker compose up -d postgres-dev postgres-test
docker compose ps
docker compose exec postgres-dev pg_isready -U stms -d stms
docker compose exec postgres-test pg_isready -U stms_test -d stms_test
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose down
```

### Task 2.5 — Dedicated PostgreSQL integration-test harness

**Goal:** Add pytest infrastructure that connects only to the dedicated Compose
test database and proves real synchronous PostgreSQL connectivity.

**Files:** `pyproject.toml`, `tests/integration/__init__.py`,
`tests/integration/conftest.py`, `tests/integration/test_database_connection.py`,
`.env.example`, and focused README instructions.

**Learning points:** pytest markers and fixtures, integration versus unit tests,
test database safety guards, Engine disposal, connection cleanup, and why SQLite
cannot prove PostgreSQL semantics.

**Environment:** Docker Desktop and the healthy `postgres-test` service are
required.

**Acceptance:**

- Integration tests require an explicit `STMS_TEST_DATABASE_URL` using
  `postgresql+psycopg`; absence or any SQLite/non-PostgreSQL URL fails with a
  clear setup message when integration tests are requested.
- A safety guard rejects a test URL equal to `STMS_DATABASE_URL` and rejects a
  database name that is not explicitly test-only.
- Fixtures create synchronous connections/Sessions against the dedicated test
  service and always roll back/close/dispose resources as appropriate.
- A smoke test executes `SELECT 1` and verifies the connected database is the
  dedicated test database, not the development database.
- No product table, repository, service, or API feature is introduced.

**Tests and verification:**

```powershell
docker compose up -d postgres-test
$env:STMS_TEST_DATABASE_URL = "postgresql+psycopg://stms_test:stms_test@127.0.0.1:5433/stms_test"
uv run pytest -m integration tests/integration/test_database_connection.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose down
```

### Task 2.6 — Initial baseline migration and round-trip verification

**Goal:** Create the first Alembic baseline revision and prove an empty dedicated
PostgreSQL test database can upgrade, downgrade to base, and upgrade again.

**Files:** one revision under `alembic/versions/`, migration integration tests
such as `tests/integration/test_migrations.py`, and corrections to existing
Alembic/test support only.

**Learning points:** revision identifiers, `upgrade` and `downgrade`, the
`alembic_version` table, migration ordering, empty-database reproducibility, and
safe destructive testing against a disposable database only.

**Environment:** Docker Desktop and the dedicated `postgres-test` service are
required. Downgrade commands must never target the development database.

**Acceptance:**

- The baseline revision is deterministic and contains no user, authentication,
  project, task, or other product table.
- Starting from the empty dedicated test database, `upgrade head`, one full
  `downgrade base`, and a second `upgrade head` all succeed.
- Tests verify the Alembic revision state after each transition and confirm no
  unexpected product schema was created.
- Autogeneration check reports no unexplained metadata/database drift after the
  final upgrade.
- The migration test has safety guards that refuse the development database and
  all non-PostgreSQL URLs.

**Tests and verification:**

```powershell
docker compose up -d postgres-test
$env:STMS_TEST_DATABASE_URL = "postgresql+psycopg://stms_test:stms_test@127.0.0.1:5433/stms_test"
$env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
uv run alembic check
uv run pytest -m integration tests/integration/test_migrations.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose down
```

### Task 2.7 — Database-aware readiness endpoint

**Goal:** Add unversioned `GET /health/ready`, which reports whether PostgreSQL
can answer a minimal query while leaving `/health/live` independent of the
database.

**Files:** `app/api/health.py`, `app/schemas/health.py`, a minimal database probe
module under `app/db/`, existing router/session wiring as required, focused API
tests, and PostgreSQL readiness integration tests.

**Learning points:** liveness versus readiness, FastAPI dependencies, `SELECT 1`,
connection timeout/failure translation, HTTP 200 versus 503, stable response
schemas, and resource cleanup.

**Environment:** Docker Desktop and the dedicated PostgreSQL test service are
required for acceptance, even though isolated failure translation can also be
unit-tested with a controlled dependency.

**Acceptance:**

- `GET /health/ready` remains outside `/api/v1`, returns 200 with
  `{"status":"ok"}` when PostgreSQL answers, and returns 503 with a stable
  `{"status":"unavailable"}` response when it cannot connect.
- The probe uses synchronous SQLAlchemy and a bounded connection attempt, closes
  resources, catches only expected database failures, and never exposes the URL,
  credentials, driver exception, or stack trace in the response.
- `/health/live` continues to return 200 even while PostgreSQL is unavailable.
- OpenAPI documents the readiness success/failure schemas and status codes.
- Tests cover success against real PostgreSQL, controlled failure, actual app
  routing, response JSON, OpenAPI, and liveness independence.

**Tests and verification:**

```powershell
docker compose up -d postgres-test
$env:STMS_TEST_DATABASE_URL = "postgresql+psycopg://stms_test:stms_test@127.0.0.1:5433/stms_test"
$env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL
uv run pytest tests/test_health.py tests/integration/test_readiness.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose down
```

For the manual readiness check, start the application in one PowerShell window:

```powershell
$env:STMS_DATABASE_URL = "postgresql+psycopg://stms_test:stms_test@127.0.0.1:5433/stms_test"
uv run fastapi dev app/main.py
```

In a second window, request both health endpoints, stop only `postgres-test`,
request them again, then restart the service. The expected transition is
readiness 200 → 503 → 200, while liveness remains 200 throughout:

```powershell
curl.exe -i http://127.0.0.1:8000/health/live
curl.exe -i http://127.0.0.1:8000/health/ready
docker compose stop postgres-test
curl.exe -i http://127.0.0.1:8000/health/live
curl.exe -i http://127.0.0.1:8000/health/ready
docker compose start postgres-test
```

### Task 2.8 — Stage 2 documentation and final PostgreSQL verification

**Goal:** Document the complete Stage 2 workflow and perform a clean final
verification of Compose, migration round trips, readiness behavior, integration
tests, and all quality gates.

**Files:** `README.md` and corrections to existing Stage 2 files only.

**Learning points:** executable infrastructure documentation, service lifecycle,
database recovery/readiness interpretation, migration safety, and separating
development data from disposable test data.

**Environment:** Docker Desktop must be installed and running. Both Compose
PostgreSQL services must be usable.

**Acceptance:**

- README documents Docker Desktop prerequisites, development/test URLs, service
  startup and shutdown, migration commands, integration tests, readiness, and
  the rule that real `.env` files and database credentials are not committed.
- A newly created dedicated test database upgrades to head, downgrades to base,
  and re-upgrades; development data is never reset during verification.
- Readiness is observed as 200 with PostgreSQL available and 503 when the test
  service is unavailable, while liveness remains 200 in both states.
- The complete suite uses PostgreSQL for integration behavior and contains no
  SQLite dependency, async SQLAlchemy, product model, authentication, project,
  or task implementation.
- All Stage 2 tests and quality commands pass with recorded real output, then
  work stops for owner confirmation before Stage 3.

**Tests and verification:**

```powershell
docker compose config
docker compose up -d postgres-dev postgres-test
docker compose ps
$env:STMS_TEST_DATABASE_URL = "postgresql+psycopg://stms_test:stms_test@127.0.0.1:5433/stms_test"
$env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
uv run alembic check
uv run pytest
uv run pytest -W always -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose down
```

Repeat the Task 2.7 manual HTTP sequence during this final verification and
record the actual liveness/readiness status codes before and after stopping the
test database.

### Stage 2 completion criteria

- SQLAlchemy 2, Psycopg, and Alembic are locked and use synchronous APIs only.
- Typed configuration supplies PostgreSQL URLs without committing credentials.
- Empty `Base` metadata and explicit Session lifetime infrastructure exist
  without product models.
- Separate development and test PostgreSQL services are reproducible with
  Docker Compose.
- A dedicated PostgreSQL test database proves connectivity and migration
  upgrade/downgrade/re-upgrade behavior; SQLite is not used as a substitute.
- `/health/ready` accurately reflects PostgreSQL availability while
  `/health/live` remains process-only.
- All tests, Ruff checks, formatting, mypy, lock, and diff checks pass, then the
  stage stops for owner confirmation.

## Later stages

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
