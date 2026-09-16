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

The Stage 2 planning check could not invoke `docker` directly from the Codex
sandbox. Later diagnosis confirmed that Docker Desktop was installed and usable
through its absolute executable path in an allowed environment; this was a
sandbox access/command-resolution limitation, not an absent local Docker CLI.
Tasks 2.1 through 2.3 can be completed and fully accepted without Docker because
they do not open a database connection. Before Task 2.4, start Docker Desktop,
then verify all of the following:

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
  `stms`, plus `postgres-test` at default host port 5433 with local sample database
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
- The default test host port is 5433; an explicit `STMS_POSTGRES_TEST_PORT`
  override is allowed only when the test URL uses the same dedicated port.
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

## Stage 3 — Registration

Stage 3 adds one complete write path: an unauthenticated client can register a
new user through `POST /api/v1/auth/register`. The implementation must follow
`Router -> Service -> Repository -> SQLAlchemy`, keep transaction ownership in
the service, and use the dedicated PostgreSQL test database for persistence
verification.

This stage does not add login, access or refresh JWTs, authentication
dependencies, logout, administrators, password reset/change, user profile
editing, projects, or tasks. The default FastAPI validation response may remain
in place; the cross-application error envelope and request IDs remain Stage 8
work. No registration task may pre-implement a later-stage capability.

### Stage 3 data and security decisions

- `users.id` is a PostgreSQL UUID primary key with a database-generated default.
- `users.email` stores only the canonical email, is non-null, is limited to 254
  characters, and has a named database unique constraint. There is no separate
  display-email column in this stage.
- Canonicalization is one shared operation: trim surrounding whitespace,
  validate the address, use the validator's normalized domain representation,
  then Unicode-casefold the complete address. The canonical value is persisted
  and returned. This intentionally treats the whole address as
  case-insensitive; every application write must use the same helper.
- A repository lookup can provide an early duplicate result, but it cannot make
  registration race-safe. The named unique constraint is the final defense when
  concurrent requests both pass that lookup. The service must translate only
  that constraint's integrity failure into the duplicate-email domain error and
  roll back the transaction.
- `users.password_hash` is non-null and limited to 255 characters. Passwords
  are never persisted or logged in plaintext, and Argon2id is the only password
  hashing algorithm introduced in this stage.
- Registration passwords are 12–128 Unicode characters. They are not trimmed or
  case-normalized, all-whitespace values are rejected, and no composition rule
  is added. A secret-aware schema type must keep the value out of model
  representations and validation output.
- `users.created_at` and `users.updated_at` are non-null PostgreSQL
  `TIMESTAMP WITH TIME ZONE` values with database defaults. They are interpreted
  and exposed as timezone-aware UTC datetimes; initial registration sets both.
- The public user response contains exactly `id`, `email`, `created_at`, and
  `updated_at`. It never contains `password`, `password_hash`, credentials, or
  internal database errors.
- Every schema change has an Alembic revision. Migration verification runs only
  against `postgres-test` and must prove upgrade, downgrade to revision
  `2f6a8c1d4b90`, and re-upgrade to the new head. Never downgrade or clean the
  development database or remove its named volume.

Each task below is sized for approximately 1–2 focused hours and ends with a
review stop. Use the absolute Docker executable documented in the README when
the Codex sandbox cannot resolve or execute `docker` directly. Replace database
URL placeholders locally and never paste credentials into command output, logs,
tests, or commits.

### Task 3.1 — User ORM model and reversible migration

**Goal:** Add the minimal SQLAlchemy `User` model and one Alembic revision that
creates the `users` table. Establish the UUID, bounded fields, UTC-capable
timestamps, and reversible Stage 3 schema baseline before any registration
behavior is implemented.

**Prerequisites:** Stage 2 is owner-approved; revision `2f6a8c1d4b90` is the
single Alembic head; `Base.metadata` is empty; the dedicated `postgres-test`
target and its destructive-migration safety guard are available.

**Files:**

- Add `app/models/__init__.py` and `app/models/user.py`.
- Update `app/db/base.py` or `alembic/env.py` only as needed to register model
  metadata without creating an Engine or opening a connection at import time.
- Add one revision under `alembic/versions/` whose `down_revision` is
  `2f6a8c1d4b90`.
- Add focused model-metadata and migration integration tests.

**Implementation scope:** Map only `id`, `email`, `password_hash`, `created_at`,
and `updated_at`. Use a PostgreSQL UUID primary key with a database default,
`VARCHAR(254)` for email, `VARCHAR(255)` for the hash storage field, and
timezone-aware PostgreSQL timestamps with database defaults. The revision must
create and reversibly drop only `users`, and its `down_revision` must be
`2f6a8c1d4b90`.

**Explicitly not included:** No email normalization or email unique constraint;
those belong to Task 3.2. Do not add schemas, password policy/hashing,
repositories, services, routers, registration behavior, or dependencies.

**Learning points:** SQLAlchemy 2 typed mappings; PostgreSQL UUID and
timezone-aware server defaults; side-effect-free, reversible Alembic migrations.

**Acceptance criteria:**

- `users` has UUID `id`, an `email` storage field (`VARCHAR(254)`), `password_hash`
  (`VARCHAR(255)`), and timezone-aware `created_at`/`updated_at` columns.
- All columns are non-null; UUID and timestamps have database defaults.
- `users.email` is not yet unique; the named database constraint is deliberately
  deferred to Task 3.2.
- The migration upgrade creates only `users`; downgrade drops only `users`.
- Importing the model, Alembic environment, or application does not connect to
  PostgreSQL.
- A test database round trip reaches the new head, downgrades to
  `2f6a8c1d4b90`, and re-upgrades; `alembic check` reports no drift.

**Automated tests:** Connection-free metadata tests assert the exact columns,
types, nullability, defaults, and absence of an email unique constraint. A
marked PostgreSQL migration test asserts the parent revision, upgrade-created
table, downgrade to `2f6a8c1d4b90`, re-upgrade, and drift-free metadata.

**Verification commands:**

```powershell
docker compose up -d --wait postgres-test
$env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL
uv run alembic upgrade head
uv run alembic current
uv run alembic downgrade 2f6a8c1d4b90
uv run alembic upgrade head
uv run alembic check
uv run pytest -m integration tests/integration/test_user_migration.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose stop postgres-test
```

**Stop boundary:** Stop after the model/migration diff and Task 3.1 checks are
reported. Do not add normalization, uniqueness, schemas, hashing, or registration
code before owner confirmation.

### Task 3.2 — Email normalization and database uniqueness contract

**Goal:** Implement one reusable email normalization operation and prove that
all case/whitespace variants intended to identify the same account produce one
stored value, while PostgreSQL remains the final duplicate-write guard.

**Prerequisites:** Task 3.1 is owner-approved; the `users` table and ORM mapping
exist without an email unique constraint; its revision is the current head.

**Files:**

- Add a small email normalization module under `app/core/`.
- Add and lock the maintained email-validation dependency needed by the
  normalization helper.
- Add ordinary normalization tests and PostgreSQL uniqueness tests.
- Update the `User` table metadata with the named email unique constraint.
- Add one new Alembic revision, parented to the Task 3.1 revision, that adds only
  that constraint and removes it on downgrade.

**Implementation scope:** Add one deterministic helper that trims only
surrounding email whitespace, validates the address with a maintained library,
uses the validator-normalized domain, casefolds the full result, and rejects a
canonical value longer than 254 characters. Lock the email-validation dependency
in this task. Name the final database defense `uq_users_email` consistently in
ORM metadata and migration.

**Explicitly not included:** Do not change password behavior, create Pydantic
registration/public-user schemas, add repositories/services/routes, translate
duplicates to HTTP 409, or alter the Task 3.1 revision after it has been
accepted.

**Learning points:** validation versus canonicalization; Unicode `casefold` and
length boundaries; database uniqueness as the final concurrency invariant.

**Acceptance criteria:**

- Surrounding whitespace is removed before validation, the domain uses the
  validator's normalized representation, and the full address is casefolded.
- Equivalent mixed-case inputs produce the same value; malformed and
  over-254-character canonical values are rejected safely.
- The helper is deterministic and does not log or retain raw input.
- A real PostgreSQL test proves two inserts of the same canonical email cannot
  both commit and identifies the named unique constraint.
- No login or email-delivery behavior is introduced.

**Automated tests:** Ordinary tests cover valid normalization, surrounding
whitespace, mixed case, Unicode/domain behavior, malformed input, determinism,
and the 254-character boundary. A marked PostgreSQL test performs duplicate
canonical inserts, identifies `uq_users_email`, and verifies the new migration
can upgrade, downgrade all the way to `2f6a8c1d4b90`, re-upgrade, and pass
`alembic check`.

**Verification commands:**

```powershell
uv run pytest tests/test_email_normalization.py
docker compose up -d --wait postgres-test
$env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL
uv run pytest -m integration tests/integration/test_user_email_uniqueness.py
uv run alembic downgrade 2f6a8c1d4b90
uv run alembic upgrade head
uv run alembic check
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose stop postgres-test
```

**Stop boundary:** Stop after normalization and the named database uniqueness
contract are verified. Do not add registration schemas or consume the helper in
a registration workflow before owner confirmation.

### Task 3.3 — Registration and public user schemas

**Goal:** Define bounded Pydantic request and response contracts for
registration, including email validation and secret-safe password handling,
without adding the HTTP endpoint yet.

**Prerequisites:** Task 3.2 is owner-approved; its normalization helper and
email-validation dependency are available; the final stored email contract is
documented.

**Files:**

- Add `app/schemas/user.py` and update `app/schemas/__init__.py` if exports are
  used.
- Reuse the email-validation dependency locked by Task 3.2; update
  `pyproject.toml` and `uv.lock` only if compatibility requires a correction.
- Add ordinary schema tests.

**Implementation scope:** Define a request model containing exactly `email` and
secret-aware `password`, and a public response model containing exactly `id`,
`email`, `created_at`, and `updated_at`. Reuse Task 3.2 email validation and
canonicalization. Configure ORM-attribute serialization only for the explicit
public allowlist and enforce timezone-aware response datetimes.

**Explicitly not included:** Do not yet enforce the 12–128 password policy or
hash passwords; both belong to Task 3.4. Do not create a repository, service,
HTTP route, migration, or new dependency.

**Learning points:** Pydantic 2 external contracts; secret-aware request values;
ORM-to-schema public-field allowlists.

**Acceptance criteria:**

- The registration request accepts only email and a secret-aware password;
  email is validated/canonicalized, while password policy enforcement is
  deliberately deferred to Task 3.4.
- The public user schema exposes exactly `id`, canonical `email`, `created_at`,
  and `updated_at`, with UUID and timezone-aware datetime types.
- Schema serialization from a `User` never emits `password_hash`, even if that
  attribute exists on the source object.
- Validation errors and object representations do not reveal the submitted
  password.
- Ordinary tests cover valid input, extra-field rejection, secret-safe password
  representation/errors, invalid/oversized email, timezone-aware UTC output,
  and hash non-disclosure.

**Automated tests:** Schema-only tests use no Session or Docker. They assert the
exact request/output fields, canonical email result, invalid email rejection,
secret redaction, timezone awareness, ORM serialization, and the impossibility
of emitting `password_hash`.

**Verification commands:**

```powershell
uv run pytest tests/test_user_schemas.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
uv lock --check
git diff --check
```

**Stop boundary:** Stop after schema contracts pass. Do not implement password
policy/hashing, persistence, a service, or an HTTP endpoint before owner
confirmation.

### Task 3.4 — Password policy and Argon2id hashing

**Goal:** Add a small password security component that creates and verifies
Argon2id hashes using a maintained library, with no database or HTTP concerns.

**Prerequisites:** Task 3.3 is owner-approved and supplies the secret-aware
registration request type; no password-hashing dependency is present yet.

**Files:**

- Add the selected Argon2id-capable runtime dependency (prefer
  `pwdlib[argon2]` after confirming Python 3.14 compatibility) to
  `pyproject.toml` and `uv.lock`.
- Add a focused module such as `app/core/security.py`.
- Update the registration request schema to reuse the centralized password
  policy rather than duplicating its bounds.
- Add ordinary password-security tests.

**Implementation scope:** Introduce the password dependency only now. Centralize
the 12–128 Unicode-character policy, reject all-whitespace values, preserve the
password exactly without trimming/truncation/case conversion, and provide small
Argon2id hash/verify functions using maintained library defaults.

**Explicitly not included:** Do not persist users, query email, create a
repository/service/router, open a Session, return HTTP errors, implement login,
JWT, token behavior, or add composition rules such as mandatory character
classes.

**Learning points:** password policy versus input mutation; salted adaptive
Argon2id hashing; safe verification and diagnostic non-disclosure.

**Acceptance criteria:**

- New hashes identify Argon2id and use the library's reviewed recommended
  parameters; no SHA, bcrypt, plaintext, or reversible fallback exists.
- The helper verifies the correct password and rejects incorrect or malformed
  hashes without leaking either input.
- The same password produces different salted hashes.
- Password policy is centralized rather than duplicated across router, service,
  and hashing code.
- Tests and logs never print plaintext passwords or complete hashes.

**Automated tests:** Pure tests cover lengths 11/12/128/129, empty and
all-whitespace values, Unicode character counting, preservation of allowed
spaces, no truncation, Argon2id identification, correct/incorrect/malformed
verification, salted non-determinism, and secret-safe representations/errors.

**Verification commands:**

```powershell
uv run pytest tests/test_security.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
uv lock --check
git diff --check
```

**Stop boundary:** Stop after policy and standalone Argon2id primitives pass.
Do not store a user or begin the registration use case before owner confirmation.

### Task 3.5 — User repository

**Goal:** Add the smallest persistence adapter needed by registration: lookup by
canonical email and add/flush a new user. It must not normalize input, hash
passwords, translate HTTP errors, commit, or own the transaction.

**Prerequisites:** Tasks 3.1–3.4 are owner-approved; the `User` model, canonical
email contract, public schemas, and password primitives exist; the repository
package does not yet exist.

**Files:**

- Add `app/repositories/__init__.py` and `app/repositories/users.py`.
- Add ordinary repository contract tests with a controlled Session double and
  PostgreSQL repository integration tests.

**Implementation scope:** Provide only an exact canonical-email lookup and an
add/flush operation for an already-canonical email and already-created hash.
Accept a caller-owned synchronous Session and return the mapped `User` needed by
the service.

**Explicitly not included:** Do not normalize email, validate/hash plaintext,
commit, roll back, catch/translate integrity errors, raise HTTP exceptions,
create a service/router, or add schema/migration/dependency changes.

**Learning points:** SQLAlchemy 2 exact `select` queries; identity/default loading
during `flush`; repository persistence versus use-case transaction ownership.

**Acceptance criteria:**

- Lookup uses an exact canonical-email predicate and returns `User | None`.
- Create adds and flushes the model so database defaults are available, but
  never commits or rolls back independently.
- Repository code contains no FastAPI types, status codes, password hashing, or
  exception text intended for clients.
- Real PostgreSQL tests prove lookup/create behavior and leave the dedicated
  test database clean through rollback or explicit fixture cleanup.

**Automated tests:** Controlled-Session tests assert the exact SQLAlchemy
predicate, add/flush calls, returned value, and absence of commit/rollback.
Marked PostgreSQL tests cover missing/found lookup, generated UUID/timestamps
after flush, exact canonical matching, and isolation/cleanup.

**Verification commands:**

```powershell
uv run pytest tests/test_user_repository.py
docker compose up -d --wait postgres-test
uv run pytest -m integration tests/integration/test_user_repository.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose stop postgres-test
```

**Stop boundary:** Stop once repository persistence contracts pass. Do not
orchestrate registration or expose HTTP behavior before owner confirmation.

### Task 3.6 — Registration service and transaction boundary

**Goal:** Orchestrate the successful registration use case—canonicalization,
password-policy enforcement, Argon2id hashing, repository persistence, and the
write transaction—in a service with no HTTP dependency.

**Prerequisites:** Task 3.5 is owner-approved; the normalization, password,
schema/model, and repository contracts are available; no registration service
or HTTP route exists.

**Files:**

- Add `app/services/__init__.py` and a registration service module.
- Add the minimal registration-domain exception under `app/core/`.
- Add ordinary service tests with controlled repository/session/hash doubles.

**Implementation scope:** Accept validated registration input and a caller-
supplied Session, normalize email, enforce password policy, hash the plaintext,
construct/persist the user through the repository, commit once, refresh only if
needed for public fields, and roll back on failures. Return a `User` or internal
result suitable for the public schema.

**Explicitly not included:** Do not add the duplicate-email pre-check or inspect
unique-constraint errors; both conflict paths belong to Task 3.8. Do not add an
HTTP router/status code, migration, dependency, login, or token behavior.

**Learning points:** use-case transaction ownership; dependency-injected
orchestration; rollback guarantees across hashing and persistence failures.

**Acceptance criteria:**

- The service canonicalizes before persistence and hashes only an
  accepted plaintext password.
- One successful call persists one user and commits once; no plaintext password
  reaches the repository model after hashing.
- Hashing or persistence failure rolls back once and re-raises a safe domain or
  internal error without embedding raw SQLAlchemy/Psycopg text.
- The service owns the write transaction; repositories still never commit.
- No session, password, complete hash, database URL, or raw exception is logged.

**Automated tests:** Service-only tests use controlled normalization, policy,
hasher, repository, and Session doubles. They cover the successful call order,
one commit, returned user, exact password preservation until hashing, no
plaintext persistence, hash/persistence/commit failure rollback, and absence of
HTTP or sensitive diagnostic behavior.

**Verification commands:**

```powershell
uv run pytest tests/test_registration_service.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
uv lock --check
git diff --check
```

**Stop boundary:** Stop after the success-path transaction orchestration and
generic rollback behavior pass. Do not add the route or duplicate/conflict
handling before owner confirmation.

### Task 3.7 — `POST /api/v1/auth/register`

**Goal:** Expose the successful registration path through the versioned API,
wiring request parsing and Session dependency to the service while keeping the
router free of persistence and hashing logic.

**Prerequisites:** Task 3.6 is owner-approved; its success-path service and
transaction contract exist; the versioned router is still empty.

**Files:**

- Add the versioned auth endpoint/router module and package exports.
- Update `app/api/v1/router.py` to include the auth router.
- Add ordinary API tests using dependency overrides or fakes, with no Docker
  dependency.

**Implementation scope:** Add only the successful versioned registration route,
inject the existing request-scoped Session, call the service once, serialize the
public allowlist, return 201, and document the request, 201, and default 422
contracts.

**Explicitly not included:** Do not implement duplicate-email 409 translation or
database-race handling until Task 3.8. Do not query/hash in the router, add a
migration/dependency, or implement login, tokens, logout, current-user, project,
or task routes.

**Learning points:** FastAPI router and dependency composition; `201 Created`
response-model disclosure boundaries; OpenAPI request/response contracts.

**Acceptance criteria:**

- `POST /api/v1/auth/register` accepts the registration schema and returns 201
  with the public user schema; no unversioned registration route exists.
- The call chain is exactly Router -> registration Service -> User Repository ->
  SQLAlchemy Session.
- The endpoint receives the existing request-scoped Session dependency; the
  dependency still closes it in `finally`, including error paths.
- Validation failures return 422 without calling the service.
- The 201 body and OpenAPI schema cannot contain `password` or `password_hash`.
- Application import/startup still performs no database connection.

**Automated tests:** Connection-free API tests cover 201 and exact response
fields, canonical email returned from the service result, 422 without a service
call, Session dependency wiring/closure, no unversioned route, OpenAPI request/
201/422 schemas, and absence of password/hash fields.

**Verification commands:**

```powershell
uv run pytest tests/test_registration_api.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
uv lock --check
git diff --check
```

**Stop boundary:** Stop after the successful HTTP path is verified. Duplicate
registration is not accepted as complete until Task 3.8, which must not be
started without owner confirmation.

### Task 3.8 — Duplicate-email conflict and race handling

**Goal:** Complete the conflict path so both an early duplicate lookup and the
database unique-constraint race become the same safe HTTP 409 response.

**Prerequisites:** Task 3.7 is owner-approved; the route works for success; the
repository has exact lookup; `uq_users_email` is active in PostgreSQL; the
service owns commit/rollback.

**Files:**

- Update the registration service's integrity-error handling.
- Update the auth router and add a minimal documented 409 response schema if
  required.
- Extend focused service and API tests; add a PostgreSQL constraint-path test.

**Implementation scope:** Add an application-layer lookup before hashing for a
friendly early conflict, plus narrow handling of the PostgreSQL unique violation
for `uq_users_email` at flush/commit time. Both paths raise one safe domain
exception; the router maps only that exception to the same stable 409 body.
Always roll back a failed database transaction before returning control.

**Explicitly not included:** Do not treat unrelated integrity/database failures
as duplicates, expose driver/SQL details, change canonicalization, create another
migration/dependency, or add login/token/global Stage 8 error-envelope behavior.

**Learning points:** time-of-check/time-of-use races; narrow inspection of named
PostgreSQL unique violations; domain-to-HTTP conflict translation.

**Acceptance criteria:**

- A duplicate canonical email returns 409 with a stable, non-sensitive message;
  it never returns 500 or exposes SQL, driver details, URLs, credentials, or the
  submitted password.
- The service catches an integrity failure only when it identifies the named
  `uq_users_email` constraint, rolls back, and raises the same duplicate-email
  domain exception as the pre-check path.
- Unrelated integrity/database failures are not mislabeled as duplicates and
  remain sanitized.
- OpenAPI documents the 201, 409, and 422 outcomes and their response shapes.
- Tests cover mixed-case/whitespace duplicates and both pre-check and
  constraint-race paths.

**Automated tests:** Service tests cover pre-check before hashing, no commit on
early duplicate, the exact named-constraint race, rollback before translation,
and unrelated-error propagation/sanitization. API tests prove identical safe
409 responses for both domain paths. A marked real-PostgreSQL test forces the
constraint path after bypassing/staling the pre-check.

**Verification commands:**

```powershell
uv run pytest tests/test_registration_service.py tests/test_registration_api.py
docker compose up -d --wait postgres-test
uv run pytest -m integration tests/integration/test_registration_conflict.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose stop postgres-test
```

**Stop boundary:** Stop after 409 behavior is proven for both duplicate paths.
Do not broaden error infrastructure or start end-to-end Stage 3 acceptance before
owner confirmation.

### Task 3.9 — Real PostgreSQL registration integration tests

**Goal:** Verify the complete public registration call against only
`postgres-test`, including actual SQL, Argon2id persistence, canonical email,
transaction behavior, and deterministic concurrent duplicate protection.

**Prerequisites:** Tasks 3.1–3.8 are owner-approved; both Stage 3 migrations are
at one head; ordinary tests pass; only the disposable `postgres-test` target is
authorized for this task.

**Files:**

- Extend `tests/integration/conftest.py` only as needed for a migrated,
  isolated registration database/session fixture.
- Add `tests/integration/test_registration.py` and any narrowly shared test
  helpers.
- Do not change production behavior except to fix defects demonstrated here.

**Implementation scope:** Exercise the real FastAPI route through Router ->
Service -> Repository -> synchronous SQLAlchemy -> PostgreSQL. Add narrowly
scoped fixtures for migrated schema and isolation, and deterministic concurrency
coordination that proves the database final defense.

**Explicitly not included:** Do not add new product behavior, tables, migrations,
runtime dependencies, login/tokens, global error handling, or access the
development database/volume. Any production correction must be the minimum fix
for a demonstrated Stage 3 defect and be reported explicitly.

**Learning points:** FastAPI dependency overrides with real PostgreSQL;
transactional integration isolation; deterministic concurrent-registration
testing.

**Acceptance criteria:**

- A real API request returns 201 and persists one user with the canonical email,
  UUID, timezone-aware UTC timestamps, and an Argon2id hash that verifies.
- The stored hash differs from plaintext, and neither API output nor captured
  logs contains the password, hash, complete URL, credentials, or raw exception.
- Invalid input returns 422 and creates no row.
- Sequential canonical duplicates return 409 and keep one row.
- A deterministic two-session/barrier test proves that two racing registrations
  cannot both commit; one succeeds, the other takes the safe 409/domain-conflict
  path, and both Sessions are closed.
- Tests refuse non-test URLs, run only with the integration marker, and clean up
  without touching `postgres-dev` or any named volume.

**Automated tests:** Marked integration tests cover 201 persistence, canonical
email, UUID/timestamp values, Argon2id verification, 422/no row, sequential
duplicate 409/one row, deterministic concurrent one-success/one-conflict,
rollback/Session closure, and response/log secret non-disclosure.

**Verification commands:**

```powershell
docker compose up -d --wait postgres-test
$env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL
uv run alembic upgrade head
uv run pytest -m integration tests/integration/test_registration.py
uv run pytest -m integration tests/integration
uv run pytest
uv run pytest -W always -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose stop postgres-test
```

**Stop boundary:** Stop after reporting real PostgreSQL integration evidence and
any narrowly required defect fixes. Do not update final documentation or begin
Stage 4 before owner confirmation.

### Task 3.10 — Stage 3 documentation and final registration verification

**Goal:** Document the implemented registration contract and perform one clean,
evidence-based Stage 3 acceptance pass without adding later-stage behavior.

**Prerequisites:** Task 3.9 is owner-approved; production behavior and both
migrations are stable; ordinary and integration coverage exists; no unresolved
Stage 3 defect remains.

**Files:**

- Update `README.md` and only the Stage 3 documentation that the final code
  makes necessary.
- Update `.env.example` only if Stage 3 introduced a new non-secret setting.
- Do not add a business feature or another migration during documentation
  cleanup; stop and report model/migration drift instead.

**Implementation scope:** Reconcile README and Stage 3 documentation with the
actual API, policies, transaction/constraint design, safe test-database workflow,
and exact verification commands. Perform the clean final review and record real
results.

**Explicitly not included:** Do not modify User/business code, dependencies,
locks, schemas, migrations, API behavior, Docker topology, or any Stage 4+
feature. If verification exposes such a required change, stop and open a
separate corrective task.

**Learning points:** executable API documentation; clean-database migration
proof; security-focused diff, log, and OpenAPI review.

**Acceptance criteria:**

- Documentation gives safe registration examples, 201/409/422 behavior, the
  canonical-email and password policy, and explicitly states that login/JWT/
  refresh/logout/admin/password recovery are not implemented.
- Documentation explains that public users never contain `password_hash`, the
  service owns transactions, and the database unique constraint is the final
  concurrent-registration defense.
- From the dedicated test database, upgrade -> downgrade to
  `2f6a8c1d4b90` -> re-upgrade leaves Alembic at the single new head and
  `alembic check` reports no drift.
- Ordinary tests pass without Docker; all registration and existing integration
  tests pass against `postgres-test` only.
- OpenAPI documents the request, public response, 201, 409, and 422 without
  secret fields. Captured responses, logs, test output, and final diff contain no
  password, complete database URL, credentials, complete hash, or raw database
  exception.
- Ruff lint/format, mypy, lock, and diff checks pass. Only project containers
  used for verification are stopped afterward; the development named volume is
  retained. The stage stops for owner confirmation.

**Automated tests:** Run the complete ordinary suite and warning pass, all
marked integration tests against only `postgres-test`, the migration round trip
to `2f6a8c1d4b90`, `alembic check`, OpenAPI disclosure assertions, and all
quality gates. Do not add new tests unless documenting a previously missing
acceptance assertion requires a documentation-only correction to the plan.

**Verification commands:**

```powershell
docker compose config
uv run pytest
uv run pytest -W always -q
docker compose up -d --wait postgres-test
$env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL
uv run alembic upgrade head
uv run alembic downgrade 2f6a8c1d4b90
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
uv run alembic check
uv run pytest -m integration tests/integration
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose stop postgres-test
```

**Stop boundary:** Stop after the Stage 3 evidence and final Git diff are
reported. Do not commit, push, or begin login/Token work; wait for explicit owner
acceptance and a separate Stage 4 instruction.

### Stage 3 completion criteria

- One versioned registration endpoint creates only the minimal user record and
  returns a secret-free public schema.
- Canonical email behavior is explicit and shared; PostgreSQL uniqueness safely
  resolves concurrent registrations as one success and one conflict.
- Argon2id protects every stored password, with bounded secret-safe input and no
  sensitive response/log/test disclosure.
- Service-owned commit/rollback and request-owned Session closure are covered on
  success and failure paths.
- The user migration round-trips on an empty dedicated test database and matches
  SQLAlchemy metadata with no additional product tables.
- Ordinary and integration suites plus Ruff, formatting, mypy, lock, and diff
  checks pass, then the stage stops for owner confirmation.

## Later stages

## Stage 4 — Login and current user

Stage 4 adds short-lived access-token authentication on top of the accepted
Stage 3 user and password contracts. It provides login, Bearer-token validation,
an authenticated-user dependency, and read/update operations for the current
user. The implementation continues to use synchronous SQLAlchemy Sessions and
the existing Router -> Service -> Repository -> SQLAlchemy boundaries.

This stage does not add refresh tokens, token persistence, rotation, logout,
password change, revocation, a denylist, administrators, projects, or tasks.
Those capabilities remain in Stage 5 or later. Access tokens are short-lived
JWTs and are not stored in the database, so Stage 4 requires no schema change or
Alembic revision unless the accepted plan is explicitly revised first.

### Stage 4 security and API decisions

- The first result milestone is Tasks 4.1 through 4.3: access-token primitives,
  login orchestration, and `POST /api/v1/auth/login`. It ends with one usable
  login endpoint but no protected resource yet.
- Use a maintained JWT library confirmed compatible with the locked Python 3.14
  environment. Prefer PyJWT if that compatibility check succeeds; add and lock
  the dependency only in Task 4.1 and do not guess a version in advance.
- Use a fixed application-selected HMAC algorithm rather than accepting an
  algorithm from token input. The signing secret is a `SecretStr` supplied by
  environment configuration, has no committed real value, and is never logged,
  serialized, or placed in an exception.
- Access-token settings are `STMS_ACCESS_TOKEN_SECRET`,
  `STMS_ACCESS_TOKEN_TTL_MINUTES`, `STMS_ACCESS_TOKEN_ISSUER`, and
  `STMS_ACCESS_TOKEN_AUDIENCE`. The TTL defaults to 15 minutes and is limited to
  1–60 minutes. Issuer defaults to `fastapi-stms` and audience to
  `fastapi-stms-api`; token creation and validation use timezone-aware UTC.
- The signing algorithm is the application constant `HS256`; it is not an
  environment setting. The signing secret has no application default and must
  contain at least 32 characters before a token operation is allowed.
- The minimum access-token claims are `sub` (the user UUID as text), `type`
  (`access`), `iat`, `exp`, `iss`, and `aud`. Validation checks every claim,
  signature, the fixed algorithm, issuer, audience, and expiry before resolving
  a user.
- Login accepts canonicalizable email and a secret-aware, non-empty password of
  at most 128 Unicode characters without trimming or normalization. It does not
  reapply the registration minimum-length rule: syntactically valid credential
  attempts reach the same authentication decision. Failed
  account lookup and failed password verification return the same generic 401
  response and must not disclose whether the account exists.
- The login failure detail is exactly `Invalid email or password`. Access-token
  dependency failures use exactly `Could not validate credentials`; both are
  stable non-sensitive messages and both HTTP paths use status 401.
- Stage 4 login returns an access token only. Refresh-token issuance and any
  extension of the login response belong exclusively to Stage 5.
- Bearer credentials are parsed by a FastAPI dependency, but token validation
  and user lookup remain outside routers. Malformed, expired, tampered, wrong-
  type, wrong-issuer, wrong-audience, or unknown-user tokens share one safe 401
  contract with `WWW-Authenticate: Bearer`.
- Current-user responses reuse the explicit `PublicUser` allowlist. Tokens,
  passwords, `password_hash`, signing material, and database diagnostics never
  enter API responses, OpenAPI examples, logs, or test output.
- The only profile field available in the current `User` model is canonical
  email. Task 4.6 therefore limits `PATCH /api/v1/users/me` to an email change;
  it reuses normalization and `uq_users_email` race handling. It does not change
  passwords or add profile columns.

Each task below is sized for approximately 1–2 focused hours. Complete tasks in
order and stop at each declared milestone boundary for owner review.

### Task 4.1 — Access-token settings and JWT primitives

**Goal:** Add typed access-token configuration plus small, independently tested
JWT creation/validation primitives, without HTTP, database, or login behavior.

**Prerequisites:** Stage 3 is accepted and committed; the worktree is clean;
`9f3b2d6e8a41` remains the single Alembic head; no JWT dependency or Stage 4
implementation exists.

**Files:**

- Update `pyproject.toml` and `uv.lock` with one maintained JWT runtime
  dependency after confirming Python 3.14 compatibility.
- Update `app/core/config.py` and `.env.example` with non-secret access-token
  settings.
- Add a focused token module under `app/core/`, keeping password hashing in its
  existing module.
- Add connection-free configuration and token tests.

**Implementation scope:** Define fixed `HS256`, a secret-aware signing key with
no default and a 32-character minimum, a 15-minute default TTL bounded to 1–60,
issuer `fastapi-stms`, and audience `fastapi-stms-api`, using the exact setting
names declared above. Provide typed functions that issue
an access token for a UUID user ID and validate one into a minimal internal
claims value. Use aware UTC times and require `sub`, `type=access`, `iat`, `exp`,
`iss`, and `aud`.

**Explicitly not included:** Do not add a login schema/service/router,
authentication dependency, database query, refresh token, logout, password
change, migration, token table, denylist, or custom cryptography.

**Automated tests:** Cover valid creation/validation, UUID subject, fixed token
type, UTC lifetime, expiration, tampering, malformed input, wrong type, issuer,
audience, algorithm rejection, missing claims, unsafe/missing secret behavior,
and secret/token non-disclosure. Tests use controlled time/settings and never
place complete tokens in parameter IDs or assertion messages.

**Acceptance criteria:**

- Only the selected maintained JWT library and its necessary transitive
  dependencies are added; unrelated packages are not upgraded deliberately.
- Valid access claims round-trip; expired, malformed, tampered, or semantically
  invalid tokens produce one safe internal authentication error.
- The decoder pins the configured algorithm and validates issuer/audience; it
  never trusts an unverified header to select an algorithm.
- Secrets and complete tokens are absent from repr, validation errors, logs,
  test output, and committed examples.
- Application import remains connection-free and no migration is created.

**Learning points:** JWT signing versus encryption; registered claim validation;
secret-aware configuration and algorithm-confusion prevention.

**Verification commands:**

```powershell
uv sync --all-groups --locked
uv run pytest tests/test_config.py tests/test_access_tokens.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

**Stop boundary:** Stop after token primitives pass unless Tasks 4.1–4.3 were
explicitly authorized as one milestone. Do not add login or HTTP behavior early.

### Task 4.2 — Login schemas and authentication service

**Goal:** Add secret-safe login request/output contracts and a read-only service
that verifies existing credentials and issues one access token, without exposing
HTTP behavior.

**Prerequisites:** Task 4.1 is accepted; token primitives are available; Stage 3
email normalization, password verification, User repository, and PublicUser
contracts remain unchanged.

**Files:**

- Add an authentication schema module or the smallest project-consistent schema
  update for `UserLoginRequest` and `AccessTokenResponse`.
- Add an authentication/login service module.
- Add ordinary schema and service tests using controlled repository/token
  doubles.
- Update package exports only where the repository already uses them.

**Implementation scope:** Accept exactly `email` and secret-aware `password`;
the password is non-empty and at most 128 Unicode characters. Canonicalize email
with the shared helper, preserve password characters without trimming or other
normalization,
look up through the caller-owned synchronous Session, verify the stored Argon2id
hash, and issue one access JWT for the user's UUID. Return exactly
`access_token` and `token_type="bearer"` through an explicit response schema.

**Explicitly not included:** Do not add the HTTP route/status code, commit or
roll back a read-only Session, mutate the user, expose PublicUser in the login
response, issue/store refresh tokens, add a migration, or reveal whether email
lookup or password verification failed.

**Automated tests:** Cover successful call order, canonical email lookup,
password preservation until verification, correct UUID passed to token issuing,
generic failure for missing user/wrong password/malformed stored hash, no token
issuance on failure, no commit/rollback, and password/hash/token non-disclosure.

**Acceptance criteria:**

- Login input exposes only email/password and uses `SecretStr`; output exposes
  only access-token/token-type fields.
- Missing account and wrong password raise the same safe domain exception and
  present no distinguishable application message.
- Repository lookup remains exact against canonical email and receives no
  plaintext password.
- The service is read-only, contains no FastAPI types, and never commits or
  rolls back.
- No refresh-token or current-user behavior appears.

**Learning points:** authentication versus authorization; generic credential
failure contracts; orchestration with secret-minimized dependency boundaries.

**Verification commands:**

```powershell
uv run pytest tests/test_auth_schemas.py tests/test_authentication_service.py
uv run pytest tests/test_security.py tests/test_registration_service.py
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

**Stop boundary:** Stop after connection-free login orchestration passes unless
the first Stage 4 result milestone is authorized. Do not add an HTTP route or
Bearer dependency early.

### Task 4.3 — `POST /api/v1/auth/login`

**Goal:** Expose the login service through the existing versioned auth router and
return a short-lived access token with a stable generic 401 failure contract.

**Prerequisites:** Task 4.2 is accepted; login service/schema behavior is fixed;
the registration route remains stable.

**Files:**

- Update `app/api/v1/endpoints/auth.py` or split it only if the existing module
  would otherwise lose clarity.
- Extend existing strict route/OpenAPI tests and add focused login API tests.
- Update `tests/test_main.py` only to add the one planned route to its exact
  route/method allowlist.

**Implementation scope:** Add only `POST /api/v1/auth/login`, inject the existing
request-scoped synchronous Session, call the login service once, return 200 with
the explicit access-token response, and translate only the generic invalid-
credentials domain error to 401 with `WWW-Authenticate: Bearer`.

**Explicitly not included:** Do not decode Bearer tokens, resolve current users,
add protected routes, issue refresh tokens, set cookies, persist tokens, add
logout, change passwords, create a migration, or add Stage 5 response fields.

**Automated tests:** Cover 200 and exact response fields, canonicalized request,
same Session passed to the service, generic 401 for missing account and wrong
password, Bearer challenge header, 422 secret masking, Session closure, POST-
only/versioned routing, OpenAPI request/200/401/422 schemas, and unchanged
registration/health routes.

**Acceptance criteria:**

- Valid credentials return 200 with exactly `access_token` and lowercase
  `token_type="bearer"`.
- All invalid credentials use the same non-enumerating 401 body and Bearer
  challenge without exposing password, hash, token, SQL, or driver diagnostics.
- OpenAPI contains the new POST route and no refresh/logout/current-user route.
- Router contains no credential query, password verification, JWT construction,
  commit, or rollback logic.
- Stage 3 registration behavior and application import remain unchanged.

**Learning points:** HTTP authentication challenges; Router-to-Service wiring;
OpenAPI secret and response allowlists.

**Verification commands:**

```powershell
uv run pytest tests/test_login_api.py tests/test_main.py
uv run pytest tests/test_registration_api.py tests/test_registration_service.py
uv run pytest -W always -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

**Milestone boundary:** Tasks 4.1–4.3 form the first Stage 4 result milestone.
Stop after reporting a working access-token login endpoint and all focused/full
quality evidence. Do not begin token-protected requests before owner approval.

### Task 4.4 — Bearer authentication dependency and current-user resolution

**Goal:** Validate an Authorization Bearer token and resolve its subject to the
current persisted User through one reusable FastAPI dependency.

**Prerequisites:** The first Stage 4 milestone is accepted; access-token parsing
and login are stable; no protected product route exists.

**Files:**

- Add `app/api/dependencies.py` if still absent.
- Extend `UserRepository` with exact UUID lookup.
- Add dependency and repository contract tests; add PostgreSQL coverage only if
  required to prove the exact lookup.

**Implementation scope:** Parse Bearer credentials without automatic unsafe
detail, validate the access token through Task 4.1 primitives, parse `sub` as a
UUID, look up the user with the supplied synchronous Session, and return the
mapped User. Every authentication failure produces the same safe 401 response
and Bearer challenge.

**Explicitly not included:** Do not create a current-user route, authorize
resource ownership, mutate users, refresh/revoke tokens, add token persistence,
or commit/rollback a read-only Session.

**Automated tests:** Cover valid resolution, missing/malformed header, wrong
scheme, expired/tampered/wrong-type token, invalid UUID subject, missing user,
exact repository UUID predicate, Session closure, no transaction writes, and
credential/token non-disclosure.

**Acceptance criteria:**

- Valid Bearer input returns the exact current User; all invalid paths return one
  401 contract with `WWW-Authenticate: Bearer`.
- Only fully validated claims influence database lookup.
- Repository UUID lookup has no HTTP/token concerns and no commit/rollback.
- OpenAPI can reference the Bearer security scheme without implying OAuth2 form
  login or refresh-token support.

**Learning points:** authentication dependency composition; validated claims to
database identity; uniform 401 behavior and enumeration resistance.

**Verification commands:**

```powershell
uv run pytest tests/test_auth_dependencies.py tests/test_user_repository.py
uv run pytest tests/test_login_api.py tests/test_registration_api.py
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

**Stop boundary:** Stop once current-user resolution is reusable and tested. Do
not expose `/users/me` or profile mutation yet.

### Task 4.5 — `GET /api/v1/users/me`

**Goal:** Add the first protected endpoint, returning the authenticated user's
existing public allowlist.

**Prerequisites:** Task 4.4 is accepted and returns a current User; `PublicUser`
remains the Stage 3 public schema.

**Files:**

- Add the versioned users endpoint/router module and include it in the v1 router.
- Add focused current-user API/OpenAPI tests.
- Minimally update the existing strict route allowlist.

**Implementation scope:** Add only `GET /api/v1/users/me`, inject the current-user
dependency, serialize through `PublicUser`, and document 200/401 plus the Bearer
security requirement.

**Explicitly not included:** Do not update profile fields, query the database in
the router, return token claims, add permissions/roles, refresh/logout behavior,
or create a migration.

**Automated tests:** Cover authenticated 200 with exact fields, absent/invalid
Bearer 401, dependency override wiring, password/hash/token non-disclosure,
GET-only/versioned routing, OpenAPI security declaration, and registration/login
regressions.

**Acceptance criteria:**

- A valid access token returns exactly `id`, `email`, `created_at`, and
  `updated_at` for its persisted subject.
- Invalid authentication never enters the endpoint and returns the uniform 401.
- No public response or OpenAPI schema contains internal User or token fields.

**Learning points:** protected-route dependency injection; ORM-to-public-schema
serialization; authentication versus resource authorization.

**Verification commands:**

```powershell
uv run pytest tests/test_current_user_api.py tests/test_auth_dependencies.py
uv run pytest tests/test_login_api.py tests/test_registration_api.py
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

**Stop boundary:** Stop after current-user read passes. Do not add PATCH behavior
or any Stage 5 endpoint.

### Task 4.6 — `PATCH /api/v1/users/me` canonical email update

**Goal:** Allow the authenticated user to change only their canonical email with
service-owned transaction and duplicate-race protection.

**Prerequisites:** Task 4.5 is accepted; current-user dependency, normalization,
`uq_users_email`, and duplicate-domain behavior are available.

**Files:**

- Add a strict email-only current-user update schema.
- Add the minimum user-update repository operation and service.
- Extend the users endpoint with PATCH and add service/API tests.
- Add a narrow PostgreSQL conflict-path integration test if existing coverage
  cannot prove the update race.

**Implementation scope:** Accept exactly one email field, canonicalize it, treat
an unchanged canonical email as an idempotent success, reject another user's
canonical email with the safe 409 contract, update through the caller-owned
Session, and let the service commit/rollback. Translate only
`uq_users_email` races.

**Explicitly not included:** Do not add name/avatar/profile columns, password
confirmation/change, email verification, account deletion, token revocation,
refresh/logout, or a migration.

**Automated tests:** Cover normalization, extra/missing-field rejection,
idempotent same email, successful update/commit, early duplicate, named-
constraint race/rollback, unrelated integrity errors, authenticated ownership,
401, 409, exact PublicUser response, and secret/internal-error non-disclosure.

**Acceptance criteria:**

- PATCH changes only the current user's canonical email and returns PublicUser.
- Service owns one commit/rollback boundary; Repository never commits or raises
  HTTP errors.
- Application lookup plus `uq_users_email` preserve uniqueness under races.
- No schema/migration/token/password behavior changes.

**Learning points:** PATCH allowlists and idempotency; authenticated self-service
ownership; optimistic checks plus database race defense.

**Verification commands:**

```powershell
uv run pytest tests/test_current_user_service.py tests/test_current_user_api.py
uv run pytest tests/test_registration_service.py tests/test_registration_api.py
uv run pytest -W always -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

**Stop boundary:** Stop after the email-only profile update is proven. Do not
add password changes, token revocation, refresh, or logout.

### Task 4.7 — Real PostgreSQL authentication integration tests

**Goal:** Prove login, Bearer authentication, current-user read, and canonical
email update through the public API against only `postgres-test`.

**Prerequisites:** Tasks 4.1–4.6 are accepted; no unresolved Stage 4 defect
remains; the dedicated test database safety guard is active.

**Files:**

- Add narrowly scoped authentication/current-user integration tests and only
  the fixtures required for per-request synchronous Sessions and exact cleanup.
- Change production code only for the minimum Stage 4 defect demonstrated by a
  real test, and report it explicitly.

**Implementation scope:** Exercise real registration/login, Argon2id
verification, access-token use, persisted-user lookup, current-user read, email
update, duplicate race behavior, transaction cleanup, and Session closure.

**Explicitly not included:** Do not test or implement refresh tokens, logout,
password change, token persistence/revocation, Stage 5 tables, or another
migration.

**Automated tests:** Cover login 200; generic wrong-email/wrong-password 401;
valid current-user 200; expired/tampered/wrong-claim/unknown-user 401; successful
canonical email update; duplicate 409; old-email login failure/new-email login
success; independent Session closure; exact cleanup; and absence of passwords,
hashes, complete tokens, URLs, credentials, or raw database errors.

**Acceptance criteria:**

- Public HTTP behavior traverses Router -> Service/dependency -> Repository ->
  synchronous Session -> PostgreSQL.
- Valid tokens resolve only their UUID subject; invalid tokens and credentials
  share the documented safe boundaries.
- Profile changes persist atomically without breaking registration uniqueness.
- Tests run only against guarded `postgres-test`, leave no rows behind, and do
  not touch `postgres-dev` or its volume.

**Learning points:** end-to-end authentication proof; token/database identity
consistency; committed-data cleanup and request Session lifetime.

**Verification commands:**

```powershell
docker compose up -d --wait postgres-test
$env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL
uv run alembic upgrade head
Remove-Item Env:STMS_DATABASE_URL
uv run pytest -m integration tests/integration/test_authentication.py
uv run pytest -m integration tests/integration
uv run pytest -W always -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose stop postgres-test
```

**Stop boundary:** Stop after real Stage 4 behavior is proven. Do not update
final documentation or begin Stage 5 before owner approval.

### Task 4.8 — Stage 4 documentation and final authentication verification

**Goal:** Reconcile documentation/OpenAPI with the implemented access-token
authentication and perform one clean Stage 4 acceptance pass.

**Prerequisites:** Task 4.7 is accepted; production behavior is stable; ordinary
and integration coverage exists; no Stage 4 defect remains.

**Files:**

- Update `README.md` and only the Stage 4 documentation made necessary by final
  behavior.
- Update `.env.example` only for the already implemented non-secret JWT setting
  names and safe placeholders.

**Implementation scope:** Document login, Bearer usage, current-user GET/PATCH,
token lifetime/claims, generic 401, email-only update, exact routes, safe local
configuration, and verification commands. Run the final ordinary, integration,
OpenAPI, migration-head, quality, and sensitive-diff review.

**Explicitly not included:** Do not modify business behavior, migrations,
dependencies, Docker topology, or add refresh/logout/password change. A defect
requiring such a change must become a separate corrective task.

**Automated tests:** Run all ordinary and marked integration tests, warnings,
OpenAPI disclosure assertions, Ruff, formatting, mypy, lock/diff checks, and
confirm the existing single migration head has no metadata drift.

**Acceptance criteria:**

- Documentation matches actual login/current-user request, response, 401, and
  Bearer contracts and explicitly defers every Stage 5 feature.
- Valid, invalid, expired, and tampered access tokens plus authenticated current-
  user behavior are proven against real PostgreSQL.
- OpenAPI and final diff expose no password/hash/secret/complete token/internal
  error; no real `.env` is tracked.
- All quality gates pass, only `postgres-test` is stopped afterward, and Stage 4
  stops for owner confirmation.

**Learning points:** executable authentication documentation; final security
surface review; stage-level evidence and recoverable Git boundaries.

**Verification commands:**

```powershell
uv run pytest
uv run pytest -W always -q
docker compose up -d --wait postgres-test
$env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
uv run alembic check
Remove-Item Env:STMS_DATABASE_URL
uv run pytest -m integration tests/integration
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose stop postgres-test
```

**Stop boundary:** Stop after Stage 4 evidence and final diff are reported. Do
not commit, push, or begin refresh-token/password-change work without explicit
owner confirmation.

### Stage 4 completion criteria

- `POST /api/v1/auth/login` issues only a bounded short-lived access JWT for
  valid credentials and uses one generic 401 contract for invalid credentials.
- Access-token validation pins the algorithm and validates signature, expiry,
  type, issuer, audience, and UUID subject before resolving a persisted user.
- `GET /api/v1/users/me` and email-only `PATCH /api/v1/users/me` operate only on
  the authenticated user and return the PublicUser allowlist.
- Synchronous request Sessions close reliably; read paths do not commit;
  profile-update transactions commit/rollback in the service; repositories
  never own transactions or HTTP behavior.
- Ordinary and real PostgreSQL tests prove valid/invalid/expired/tampered token
  behavior, current-user resolution, canonical email update, duplicate-race
  safety, and sensitive-data non-disclosure.
- No refresh token, token storage/rotation/revocation, logout, password change,
  Stage 5 migration, or later product behavior is present.
- Ruff, formatting, mypy, lock, migration-head/drift, OpenAPI, and diff checks
  pass, then the stage stops for owner confirmation.

### Stage 5 — Deferred authentication hardening

Preserve refresh-token persistence, rotation, revocation, logout, and password
change as a security-hardening track. It is not deleted, but it does not block
the first Agent MVP. The Agent critical path is Stage 4 -> 6 -> 7 -> 8 -> 9 ->
10 -> 11 -> 12; Stage 5 can be scheduled after the first Agent demonstration.

- **Task 5.1:** Refresh-token model, indexes, and reversible migration.
- **Task 5.2:** Random token issuance, hash-only storage, and transaction tests.
- **Task 5.3:** Atomic refresh rotation and old-token reuse rejection.
- **Task 5.4:** Refresh HTTP endpoint with secret-safe cookie/body contract.
- **Task 5.5:** Logout revocation for the presented refresh token.
- **Task 5.6:** Password change plus all-refresh-token revocation.
- **Task 5.7:** Real PostgreSQL security integration and documentation.

Each task remains approximately 1–2 focused hours and retains short-lived
stateless access-token semantics. Do not add a denylist, device management,
OAuth providers, or administrator behavior.

### Stage 6 — Agent-ready project core

Implement only project behavior required by the Agent MVP. Every query is scoped
by authenticated user ID; clients and models never supply ownership.

This stage retains the synchronous `Router -> Service -> Repository ->
SQLAlchemy` path established by authentication. The Bearer dependency supplies
the persisted current User; an HTTP client and a future Agent tool cannot send or
override project ownership. Missing and foreign-owned projects intentionally
share one safe 404 response.

#### Stage 6 fixed project contract

The requirements establish the field set and four statuses. The following
bounds and API choices are **Stage 6 planning decisions**, added here because the
earlier documents deliberately did not choose them:

- `id` and internal `user_id` are PostgreSQL UUIDs. `id` uses the existing
  `gen_random_uuid()` server default; `user_id` is a required foreign key to
  `users.id` and is derived only from authenticated context.
- `name` is a required trimmed string of 1–200 Unicode characters. Whitespace-
  only input is invalid. `description` is nullable, trimmed when supplied, and
  bounded to 2,000 characters; an empty or whitespace-only description becomes
  `None`.
- `start_date` and `target_date` are nullable SQL/Python calendar dates. When
  both are present, `target_date >= start_date`. Clearing either date removes the
  comparison until both are present again; no timezone conversion applies to a
  calendar date.
- `status` is stored as a non-null `VARCHAR(20)` protected by a named database
  check and represented publicly by a string `ProjectStatus` enum with exactly
  `NOT_STARTED`, `IN_PROGRESS`, `COMPLETED`, and `ARCHIVED`. The server default
  is `NOT_STARTED`; create input cannot override it.
- Ordinary PATCH may set `NOT_STARTED`, `IN_PROGRESS`, or `COMPLETED` and may
  change any editable non-ownership field. It cannot set `ARCHIVED`; archiving
  uses `POST /api/v1/projects/{project_id}/archive`. Repeating archive returns
  200 with the same public project and performs no write or commit. Stage 6 does
  not restore archived projects, and other PATCH operations on one return 409
  `Archived project cannot be modified`.
- Lists exclude archived projects by default. `include_archived=true` includes
  every status. Pagination is `page=1` and `page_size=20`, with minimum 1 and
  maximum page size 100. Ordering is fixed to `created_at DESC, id DESC`; no
  arbitrary sort or search parameter is accepted in this stage.
- Public project fields are `id`, `name`, `description`, `start_date`,
  `target_date`, `status`, `created_at`, and `updated_at`. Internal `user_id` is
  persisted and used for every authorization predicate but is excluded from
  create/update input, public responses, and future model-controlled tool input.
- PATCH distinguishes missing from explicit null. `description`, `start_date`,
  and `target_date` may be cleared with null; `name` and `status` may not. An
  empty object is rejected with 422. Cross-field validation combines supplied
  changes with the persisted project before a write.
- The project-specific missing response is HTTP 404 with the fixed safe detail
  `Project does not exist`. Stage 6 keeps the existing route-local `detail`
  response style and does not retrofit the planned cross-application error
  envelope into accepted Stage 1–4 APIs. Date/input errors remain 422; only an
  attempted normal update of an archived project uses the Stage 6 409 above.
- Actual mutations advance `updated_at` with an aware UTC value. Idempotent
  archive and no-op PATCH return without changing it or opening a write
  transaction. `created_at` never changes.

Task 6.1 creates every database invariant listed here in one migration. Later
Stage 6 tasks must not create a second project migration unless a separately
accepted defect proves that the fixed schema itself is wrong.

### Task 6.1 — Project decisions, ORM model, constraints, and reversible migration

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** required for migration
round-trip and structure inspection.

**Goal:** Add only the foundational Project ORM mapping and one reversible
migration, with the complete Stage 6 storage contract but no API behavior.

**Prerequisites:** Stage 4 is accepted and committed; the worktree is clean;
`9f3b2d6e8a41` is the single Alembic head; no Project model, table, or migration
exists.

**Files:** Add `app/models/project.py`, update `app/models/__init__.py` and
Alembic metadata discovery only as required by the repository's existing import
pattern, add exactly one revision under `alembic/versions/`, and add focused
model/migration tests. Do not create a schema, repository, service, or router.

**Implementation scope:** Map only `id`, `user_id`, `name`, `description`,
`start_date`, `target_date`, `status`, `created_at`, and `updated_at`. Use UUID,
bounded strings, SQL `date`, timezone-aware timestamps, and the fixed status
values above. The migration's `down_revision` is `9f3b2d6e8a41` and creates:

- primary key `pk_projects` on `id`, with server default `gen_random_uuid()`;
- foreign key `fk_projects_user_id_users` from `user_id` to `users.id`, with
  restrictive/no-cascade delete behavior;
- check `ck_projects_name_not_blank` using trimmed database text;
- check `ck_projects_status` allowing only the four exact stored values;
- check `ck_projects_target_date_not_before_start_date`, which passes when
  either date is null and otherwise requires target on/after start;
- index `ix_projects_user_id` for the mandatory ownership predicate.

`status` has server default `NOT_STARTED`; timestamps use `CURRENT_TIMESTAMP`.
The migration downgrade drops the projects table and its owned objects only.

**Explicitly not included:** No Pydantic schemas, validation functions,
Repository, Service, endpoint, pagination, Task relation, hard delete,
refresh-token table, Agent table, second migration, or modification of any
accepted revision.

**Automated tests:** Prove the exact metadata table/column set, UUID/defaults,
nullable fields, bounded strings, date/timestamp types, named PK/FK/checks/index,
status default, and metadata discovery. Against PostgreSQL, upgrade from
`9f3b2d6e8a41`, inspect the table and constraints, downgrade to that revision and
confirm `projects` is removed while `users` remains, then re-upgrade.

**Acceptance criteria:** ORM and migration match exactly; there is one new head
and one new revision; `alembic current` reaches it; downgrade/re-upgrade is
reversible; `alembic check` reports no drift; Stage 4 behavior remains green.

**Learning points:** ORM metadata versus DDL; named FK/check/index contracts;
nullable cross-field checks under PostgreSQL three-valued logic.

**Verification commands:**

```powershell
uv run pytest tests/test_project_model.py
docker compose up -d --wait postgres-test
$env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL
uv run alembic upgrade head
uv run alembic downgrade 9f3b2d6e8a41
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
uv run alembic check
Remove-Item Env:STMS_DATABASE_URL
uv run pytest -m integration tests/integration/test_project_migration.py
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose stop postgres-test
```

**Stop boundary:** Stop with the Project table and reversible migration only. Do
not begin Task 6.2 or create a second revision.

### Task 6.2 — Strict create/update/public/list schemas and date validation

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** not required.

**Goal:** Define connection-free Pydantic 2 contracts for project creation,
partial update, public output, and deterministic pagination.

**Prerequisites:** Task 6.1 is accepted; Project fields/statuses are fixed; the
model and migration remain unchanged.

**Files:** Add `app/schemas/project.py`, update schema exports only if the current
package style requires them, and add `tests/test_project_schemas.py`.

**Implementation scope:** Add one shared `ProjectStatus` in the smallest layer
that avoids Schema/Model circular imports, plus `ProjectCreate`,
`ProjectUpdate`, `PublicProject`, and `ProjectListResponse`. `ProjectCreate`
accepts only name, description, start_date, and target_date. `ProjectUpdate`
accepts optional editable fields plus a non-archived status and retains
`model_fields_set` so Services can distinguish missing from null. All request
schemas use `extra="forbid"` and safe validation messages. `PublicProject` uses
`from_attributes`, validates aware timestamps, normalizes them to UTC, and omits
`user_id`. The list response contains exactly `items`, `page`, `page_size`,
`total`, and `pages`.

Schema validation trims/bounds text, maps blank optional description to `None`,
checks create-time date order, rejects direct `ARCHIVED`, and rejects empty
PATCH. Update validation that depends on persisted values is intentionally
exposed as a pure helper/Service rule rather than faked from partial input.

**Explicitly not included:** No database access, ownership lookup, transaction,
Repository, Service orchestration, Router, pagination query, Agent schema, or
change to ORM/migration.

**Automated tests:** Cover every length boundary, whitespace behavior, nullable
and missing update fields, empty PATCH, extra ownership/internal fields, all
allowed statuses, direct archive rejection, create-time and combined-date helper
rules, exact public/list field sets, ORM-style attribute serialization, UTC
timestamps, and absence of `user_id` or internal fields.

**Acceptance criteria:** Every input/output allowlist is exact; name 1/200 passes
and 0/201 fails; description null/2,000 passes and 2,001 fails; partial update
semantics are observable; list metadata is bounded and internally consistent;
tests need no database or network.

**Learning points:** create versus partial-update schemas; missing versus null in
Pydantic 2; validation that needs persisted state.

**Verification commands:**

```powershell
uv run pytest tests/test_project_schemas.py
uv run pytest tests/test_user_schemas.py tests/test_auth_schemas.py
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

**Stop boundary:** Stop after connection-free Project schemas and validation
tests. Do not create persistence or HTTP behavior.

### Task 6.3 — Owned Repository queries and Service transaction contracts

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** not required; real
query/isolation proof belongs to Task 6.7.

**Goal:** Implement the reusable owned Project application core that HTTP and
future Agent tools can call without bypassing authorization or transactions.

**Prerequisites:** Tasks 6.1–6.2 are accepted; current-user identity and Project
schemas are stable; no Project route exists.

**Files:** Add `app/repositories/projects.py`, `app/services/projects.py`, focused
Repository query-contract tests, and `tests/test_project_service.py`. Add a safe
Project-not-found domain exception and archived-project conflict only in the
existing core exception location; do not add HTTP types there.

**Implementation scope:** `ProjectRepository` receives a caller-owned synchronous
Session and provides:

- `create(user_id, ...)`, using only service-derived ownership, `add`, and
  `flush`;
- `get_owned_by_id(project_id, user_id)`, whose SQL predicate contains both IDs;
- `list_owned(user_id, page, page_size, include_archived)` using fixed
  `created_at DESC, id DESC` and `offset/limit`;
- `count_owned(user_id, include_archived)` with the same ownership/archive
  predicate;
- the narrow mutation/flush operation needed for update/archive.

Service functions implement create, owned detail, owned list, update, and
archive. They receive the trusted current User or its UUID outside request data,
convert ORM values to public schemas, combine PATCH fields with persisted dates,
and own commit/rollback for actual writes. Missing and foreign-owned repository
results raise the same safe Project-not-found domain error. List/detail are
read-only and never commit. Empty/no-op PATCH and repeat archive do not write or
commit. Actual update/archive sets `updated_at` from an injectable aware-UTC
clock. Unexpected exceptions roll back and propagate.

**Explicitly not included:** No FastAPI imports, status codes, Router, HTTP error
mapping, real PostgreSQL test, hard delete, restore, Task relationship, Agent
Tool, generic base repository, unit-of-work framework, or AsyncSession.

**Automated tests:** Prove both-ID predicates, owner-scoped list/count parity,
fixed ordering, create ownership derivation, public allowlists, commit on actual
writes, rollback on failure, no commit on reads/no-op/idempotent archive,
persisted-state date validation, archived update conflict, missing/foreign
indistinguishability, and absence of HTTP concepts in Repository/Service.

**Acceptance criteria:** No method accepts a client `user_id`; every owned query
contains trusted ownership; Repository never commits/rolls back; Service owns all
write outcomes; read paths and no-ops stay transaction-neutral; current Stage 4
tests remain green.

**Learning points:** defense-in-depth ownership predicates; read versus write
transaction boundaries; deterministic pagination queries and count parity.

**Verification commands:**

```powershell
uv run pytest tests/test_project_repository.py tests/test_project_service.py
uv run pytest tests/test_auth_dependencies.py tests/test_current_user_service.py
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

**Milestone boundary:** Tasks 6.1–6.3 form the first Stage 6 result milestone: a
migrated, schema-validated, ownership-safe Project core with no public Project
route. Stop for review; do not begin Task 6.4 without approval.

### Task 6.4 — `POST /api/v1/projects` with exact public response

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** not required in this
task; API wiring uses controlled dependencies and Task 6.7 proves persistence.

**Goal:** Expose authenticated project creation through one versioned endpoint.

**Prerequisites:** Task 6.3 is accepted; create Service/schema behavior is fixed;
the worktree is clean.

**Files:** Add the project endpoint/router module under the existing v1 layout,
include it in `app/api/v1/router.py`, minimally update the strict route allowlist,
and add `tests/test_project_api.py` focused on create behavior/OpenAPI.

**Implementation scope:** Add only `POST /api/v1/projects`. Inject the existing
Bearer current User and the same request-scoped synchronous Session, call the
create Service once, and return 201 with `PublicProject`. Router request data
contains no `user_id`; Router contains no SQL, date business logic, or commit.

**Explicitly not included:** No list/detail/update/archive route, Project hard
delete, Task behavior, Agent Tool, new dependency, or migration.

**Automated tests:** Cover authenticated 201, exact request/response fields,
canonical text/date behavior, current User and same Session passed to Service,
401 challenge, 422 extra/internal/date input, Session closure, POST-only
versioning, OpenAPI schemas/security, and unchanged Stage 4 routes.

**Acceptance criteria:** Creation is one thin Router-to-Service call; response
omits `user_id`; OpenAPI adds only the planned POST path/method and exact
201/401/422 contract; health/registration/login/current-user remain compatible.

**Learning points:** authenticated ownership injection; 201 response allowlists;
strict OpenAPI route regression tests.

**Verification commands:**

```powershell
uv run pytest tests/test_project_api.py tests/test_main.py
uv run pytest tests/test_login_api.py tests/test_current_user_api.py
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

**Stop boundary:** Stop after authenticated Project creation. Do not expose
read/update/archive endpoints early.

### Task 6.5 — Owned project detail and deterministic paginated list

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** not required here;
Task 6.7 proves cross-user and query behavior against PostgreSQL.

**Goal:** Add ownership-safe Project detail and fixed paginated listing through
the existing Project Router.

**Prerequisites:** Task 6.4 is accepted; owned Repository/Service read contracts
and pagination response are stable.

**Files:** Extend the project endpoint and focused API tests; update shared error
response schemas only if the existing route style requires an explicit safe 404
model. Do not create a second Project service or router.

**Implementation scope:** Add `GET /api/v1/projects/{project_id}` returning
`PublicProject`, and `GET /api/v1/projects` with only `page`, `page_size`, and
`include_archived`. Map the Project-not-found domain error to 404 detail
`Project does not exist`. Both routes inject current User and the request Session,
delegate to Service, and never commit.

List response contains exactly `items`, `page`, `page_size`, `total`, and
`pages`; pages is zero when total is zero and otherwise ceiling(total/page_size).
The fixed order and archived behavior come from the Repository contract, not
client-controlled sort expressions.

**Explicitly not included:** No PATCH/archive/delete, status/name/date filters,
arbitrary sort, full-text search, cursor pagination, cross-user admin view, Task
embedding, or Agent Tool.

**Automated tests:** Cover owned detail 200, missing and foreign-owned identical
404, list parameter defaults/bounds, empty/non-empty page metadata, deterministic
Service output, include-archived forwarding, 401, malformed UUID 422, Session
closure, GET-only methods, OpenAPI and route allowlist compatibility.

**Acceptance criteria:** A user receives only owned projects; no error reveals a
foreign record; list shape/order parameters are fixed and bounded; read routes
perform no commit/rollback; no unplanned query parameter appears in OpenAPI.

**Learning points:** resource enumeration resistance; offset pagination metadata;
fixed sorting as an API contract.

**Verification commands:**

```powershell
uv run pytest tests/test_project_api.py tests/test_project_service.py
uv run pytest tests/test_auth_dependencies.py tests/test_main.py
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

**Stop boundary:** Stop with create/detail/list only. Do not implement mutation or
archive behavior.

### Task 6.6 — Project update and idempotent archive action

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** not required here;
Task 6.7 proves committed behavior and database constraints.

**Goal:** Complete the Stage 6 HTTP surface with strict partial update and a
dedicated idempotent archive action.

**Prerequisites:** Task 6.5 is accepted; Service update/archive contracts are
stable; no hard-delete route exists.

**Files:** Extend the existing Project endpoint and its focused tests. Add only
the narrow response model/error mapping needed for the fixed 404/409 contracts.

**Implementation scope:** Add `PATCH /api/v1/projects/{project_id}` and
`POST /api/v1/projects/{project_id}/archive`. PATCH delegates missing-versus-null
and persisted-date validation to the Service, returns 200 `PublicProject`, maps
missing/foreign to the same 404, and maps only archived mutation conflict to 409
`Archived project cannot be modified`. Archive returns 200; the first call
commits and advances `updated_at`, while repeated archive returns the unchanged
public value without a write or commit. No route can restore archived state.

**Explicitly not included:** No DELETE, unarchive/restore, bulk operation,
optimistic-lock version field, Task cascade, Agent approval, or Stage 7 behavior.

**Automated tests:** Cover each editable field, explicit nullable clears,
missing-field preservation, empty/extra/internal PATCH 422, combined stored date
failure, allowed status changes, direct ARCHIVED rejection, owned success,
missing/foreign 404, archived update 409, first/repeat archive, commit/rollback/
no-op behavior, exact public response, Bearer 401, OpenAPI and route allowlist.

**Acceptance criteria:** Update cannot change ownership/identity/timestamps
directly; cross-field rules use the final combined state; failure rolls back;
idempotent operations do not produce extra writes; there is still no Project
hard-delete or restore endpoint.

**Learning points:** PATCH missing/null semantics; state-action endpoints and
idempotency; safe domain-to-HTTP error mapping.

**Verification commands:**

```powershell
uv run pytest tests/test_project_api.py tests/test_project_service.py
uv run pytest tests/test_main.py tests/test_current_user_api.py
uv run pytest -W always -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

**Milestone boundary:** Tasks 6.4–6.6 form the second Stage 6 result milestone:
the complete authenticated Project HTTP API. Stop for review before real database
acceptance and documentation.

### Task 6.7 — PostgreSQL ownership/constraint integration and documentation

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** required.

**Goal:** Prove the complete Project API, ownership isolation, transaction
behavior, migration state, and documentation against only `postgres-test`.

**Prerequisites:** Tasks 6.1–6.6 are accepted; production behavior is stable;
ordinary coverage exists; no unresolved Project defect remains.

**Files:** Add narrowly scoped Project integration tests and exact-cleanup
fixtures under `tests/integration/`; update README and only Stage 6 documentation
made necessary by the accepted behavior. Modify production code only for a
minimal Stage 6 defect proven by a real test, and report it separately.

**Implementation scope:** Through public Bearer-protected HTTP routes and
independent synchronous request Sessions, prove create, detail, list, update,
archive, idempotent repeat, persistence, fixed ordering/pagination, archived
visibility, named database constraints, missing/foreign 404 equivalence, and
transaction cleanup. Use at least two real users and exact UUID-based cleanup;
never assert the entire projects/users tables are empty.

Perform a clean Project migration round trip from the pre-Stage-6 head
`9f3b2d6e8a41` to the Stage 6 head, downgrade to `9f3b2d6e8a41`, and re-upgrade.
Confirm `projects` disappears on downgrade while `users` remains, then confirm
all Project constraints/indexes return and `alembic check` has no drift.

**Explicitly not included:** No hard delete, Task table/API, Stage 5 refresh
work, Agent Tool, LangGraph, LLM SDK, new dependency, second Project migration,
`postgres-dev` operation, or volume deletion.

**Automated tests:** Cover two-user isolation for detail/list/update/archive;
client ownership-field rejection; successful create/persistence; date and status
database defenses; exact list order/page totals; default archived exclusion and
explicit inclusion; update commit/rollback; idempotent archive; Session closure;
secret/Token/SQL non-disclosure; migration upgrade/downgrade/re-upgrade.

**Acceptance criteria:** Every public Project operation traverses Router ->
Service -> owned Repository -> synchronous Session -> PostgreSQL; user A never
observes or mutates user B data; all public/error allowlists are stable; committed
test rows are precisely removed; ordinary/integration/quality gates pass; only
`postgres-test` is stopped afterward.

**Learning points:** end-to-end tenant isolation; schema/service/database
invariant layering; recoverable stage acceptance with committed-data cleanup.

**Verification commands:**

```powershell
uv run pytest
uv run pytest -W always -q
docker compose up -d --wait --force-recreate postgres-test
$env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL
uv run alembic upgrade head
uv run alembic downgrade 9f3b2d6e8a41
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
uv run alembic check
Remove-Item Env:STMS_DATABASE_URL
uv run pytest -m integration tests/integration/test_projects.py
uv run pytest -m integration tests/integration
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
docker compose stop postgres-test
```

**Stage boundary:** Task 6.7 is the third result milestone and completes Stage 6.
Stop for owner confirmation. Do not begin Stage 7 Task models or Stage 8 Agent
work.

Do not add collaboration, sharing, teams, roles, model-facing tools, hard delete,
or restore during Stage 6. The next accepted domain stage is Stage 7 only after
the owner can explain Project ownership, pagination, transaction, and migration
behavior.

### Stage 7 — Agent-ready task core

Tasks belong to both a user and one of that user's projects. Service logic owns
state transitions and dates; Repository predicates enforce ownership isolation.

This stage keeps the synchronous `Router -> Service -> Repository -> SQLAlchemy`
architecture. A future Agent tool will call the same Service; it will not receive
a Session or model-controlled `user_id`. Stage 7 contains three result milestones:
Tasks 7.1–7.3 establish the domain core, Tasks 7.4–7.6 expose the main Bearer API,
and Tasks 7.7–7.9 complete lifecycle, deletion, PostgreSQL proof, and documentation.

#### Stage 7 fixed Task contract

- Persist exactly `id`, `user_id`, `project_id`, `title`, `description`, `status`,
  `priority`, `planned_date`, `due_at`, `estimated_minutes`, `completed_at`,
  `created_at`, and `updated_at`. IDs are PostgreSQL UUIDs; `id` uses
  `gen_random_uuid()`. `user_id` and `project_id` are required internal ownership
  values and never appear in model-controlled input.
- `title` is trimmed, required, and 1–300 Unicode characters. `description` is
  nullable, trimmed, limited to 5,000 characters, and canonicalizes blank text to
  null. `estimated_minutes` is nullable and, when present, is an integer from 1
  through 1,440 inclusive.
- Status is exactly `TODO`, `IN_PROGRESS`, `COMPLETED`, or `CANCELLED`; the server
  default is `TODO`. Priority is exactly `LOW`, `MEDIUM`, `HIGH`, or `URGENT`; the
  server default is `MEDIUM`. Create input cannot override status or
  `completed_at`.
- `planned_date` is a nullable calendar date. `due_at` is a nullable timezone-aware
  instant normalized to UTC. With no user-timezone setting in the accepted
  architecture, "start of planned date" means `00:00:00 UTC`; when both values
  exist, `due_at` must be at or after that instant. `created_at`, `updated_at`, and
  `completed_at` are timezone-aware UTC values.
- `completed_at` is non-null exactly while status is `COMPLETED`. The dedicated
  completion action changes a non-completed Task to `COMPLETED` and assigns the
  server clock. Repeating completion is read-only and preserves the original
  timestamp. Leaving `COMPLETED` through an accepted status update clears
  `completed_at`. Repeating any effective status/value is a no-op with no write,
  commit, or timestamp change.
- Allowed non-completed transitions are `TODO <-> IN_PROGRESS`, either of those
  to `CANCELLED`, and `CANCELLED -> TODO`. The completion action accepts
  `TODO`, `IN_PROGRESS`, or `CANCELLED`. A completed Task may reopen to `TODO`,
  `IN_PROGRESS`, or `CANCELLED`; direct PATCH to `COMPLETED` is rejected because
  the server-owned completion timestamp belongs to the action.
- Every lookup contains authenticated `user_id`. Create first resolves the
  Project through an owner-scoped query. Storage uses a named composite ownership
  foreign key from `(project_id, user_id)` to a named unique Project key
  `(id, user_id)`, plus the direct User foreign key, so non-HTTP writes cannot
  attach a Task to another user's Project. Missing and foreign-owned Project/Task
  inputs share fixed safe 404 messages and reveal no owner.
- Public Task fields are `id`, `project_id`, `title`, `description`, `status`,
  `priority`, `planned_date`, `due_at`, `estimated_minutes`, `completed_at`,
  `created_at`, and `updated_at`. Internal `user_id` is excluded. Including
  `project_id` is safe because Project access is independently owner-scoped.
- Pagination uses `page=1`, `page_size=20`, minimum 1 and maximum 100. Filters are
  `project_id`, `status`, `priority`, `planned_from`, `planned_to`, `due_from`,
  `due_to`, `overdue`, and bounded trimmed `title` search. Range starts must not
  exceed range ends. Overdue means `due_at < now UTC` and status is neither
  `COMPLETED` nor `CANCELLED`.
- Sort fields are allowlisted to `created_at`, `updated_at`, `due_at`,
  `planned_date`, and `title`; direction is `asc` or `desc`. The default is
  `created_at desc`. Every sort appends `id` in the same direction as a stable
  tie-breaker; null date/instant values use explicit PostgreSQL `NULLS LAST`.
- Task deletion is owner-scoped hard deletion returning HTTP 204 with no body.
  This follows `docs/requirements.md`, which explicitly specifies Task deletion
  and deliberately defers soft deletion. There is no Task archive/restore field
  or endpoint in Stage 7.
- The first Stage 7 migration has `down_revision = "4d8c7a1b2e90"`. It may add
  the named `(id, user_id)` Project unique key required by the composite Task
  ownership foreign key, but it never edits the accepted Project migration.
  Task constraints/indexes are named and the migration is reversible.

### Task 7.1 — Task decisions, ORM model, ownership constraints, and reversible migration

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** required.

**Goal:** Add the complete fixed Task storage contract and one reversible
migration, without Schema, Service, Repository, or HTTP behavior.

**Prerequisites:** Stage 6 is accepted and committed; the worktree is clean;
`4d8c7a1b2e90` is the only Alembic head; no Task model or migration exists.

**Files:** Add `app/models/task.py`, minimally update model/metadata exports, add
exactly one revision under `alembic/versions/`, and add focused model and
`tests/integration/test_task_migration.py` coverage.

**Implementation scope:** Map only the thirteen fixed fields. Add named primary,
User foreign key, composite Project ownership foreign key, status/priority/title,
estimate, due/planned-date, and completed/status checks. Add the supporting named
Project `(id, user_id)` unique key in this new migration and ORM metadata. Add
only query-driven indexes for owner, Project, status, priority, due date, and the
default owner/created/id order. Downgrade removes Tasks and the new supporting
Project unique key while retaining Projects and Users.

**Explicitly not included:** No Pydantic Schema, Repository, Service, Router,
Task seed data, tags, recurrence, Agent code, or second migration. Do not edit
the Stage 6 migration.

**Automated tests:** Prove exact columns/types/nullability/defaults, enum/check
texts, all object names, metadata discovery, linear revision chain, upgrade from
`4d8c7a1b2e90`, downgrade back to it, and re-upgrade without drift.

**Real validation commands:** Run focused model tests; on guarded `postgres-test`
run `alembic upgrade <new-revision>`, inspect Tasks and the supporting Project
key, downgrade `4d8c7a1b2e90`, re-upgrade, then `alembic current`, `heads`, and
`check`; finish with Ruff, mypy, lock, and diff checks.

**Acceptance criteria:** One new head exists; PostgreSQL enforces all fixed field
and ownership invariants; downgrade removes only Stage 7 objects; existing Stage
6 tests remain green.

**Learning points:** Composite ownership foreign keys; database checks versus
service rules; reversible migration boundaries.

**Stop boundary:** Stop with Task storage only. Do not create Task schemas or
persistence/application operations.

### Task 7.2 — Strict create/update/public/list schemas and bounded inputs

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** not required.

**Goal:** Define strict Task request, public response, filter, and page contracts.

**Prerequisites:** Task 7.1 is accepted; persisted fields and enums are fixed.

**Files:** Add `app/schemas/task.py`, update schema exports only if the current
style requires it, and add `tests/test_task_schemas.py`.

**Implementation scope:** Add `TaskCreate`, `TaskUpdate`, `PublicTask`,
`TaskListQuery`, and `TaskListResponse`. Create accepts only `project_id`, title,
description, planned/due values, estimate, and priority. Update retains missing
versus explicit null, accepts editable fields and non-completed status, rejects
empty input and internal fields, and does not implement the completion action.
Normalize strings, aware datetimes, UTC, bounds, ranges, filters, sort field, and
direction exactly as the fixed contract states. Public schemas use explicit
allowlists and `from_attributes`.

**Explicitly not included:** No database query, ownership lookup, transaction,
Router, completion Service, deletion, or Agent Tool.

**Automated tests:** Cover every field allowlist; title/description/estimate
boundaries; extra/internal fields; aware/naive due times; UTC output; date order;
missing versus null; enum values; direct `COMPLETED` rejection; page limits;
filter ranges; title bound; sort allowlist/direction; ORM serialization; and
absence of `user_id`.

**Real validation commands:** Run `pytest tests/test_task_schemas.py` and affected
model/schema regression, then Ruff, format, mypy, lock, and diff checks.

**Acceptance criteria:** Invalid input fails safely before persistence; valid
input is canonical and bounded; public dict/JSON/OpenAPI-ready shapes never
contain ownership or internal fields.

**Learning points:** PATCH missing-versus-null semantics; validation at external
boundaries; stable pagination/filter schemas.

**Stop boundary:** Stop with connection-free contracts. Do not implement Task
Repository, Service, or routes.

### Task 7.3 — Owned Repository, create Service, and transaction tests

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** focused repository
integration is optional here; final proof belongs to Task 7.9.

**Goal:** Build the reusable ownership-safe Task persistence core and create use
case used later by HTTP and Agent tools.

**Prerequisites:** Tasks 7.1–7.2 are accepted; Project ownership and Task schemas
are stable.

**Files:** Add `app/repositories/tasks.py`, `app/services/tasks.py`, safe Task and
Project-not-found domain exceptions as needed, and focused repository/service
tests.

**Implementation scope:** The Repository receives a caller-owned synchronous
Session, performs owner-scoped Project/Task queries, and uses `add`/`flush`
without commit/rollback. The create Service derives `user_id`, proves the Project
belongs to it, hashes no secrets, commits once on success, rolls back unexpected
write failures, and returns `PublicTask`. Missing/foreign Project uses the same
safe absence. Design repository methods needed by later read/update/list/delete
tasks without implementing their use cases early.

**Explicitly not included:** No HTTP, pagination implementation, update/completion
state machine, deletion, new migration, or Agent Tool.

**Automated tests:** Inspect compiled ownership predicates; prove create receives
the trusted owner and owned Project; Repository receives no client owner; only
hash-free public data returns; success commits once; pre-write absence is
transaction-neutral; persistence failure rolls back and propagates; Repository
never commits or raises HTTP errors.

**Real validation commands:** Run Task repository/service focused tests and
Project regression, then full ordinary pytest, Ruff, format, mypy, lock, and diff.

**Acceptance criteria:** A validated Task can be created only below an owned
Project through one caller Session and one Service-owned transaction; no public
HTTP path exists yet.

**Learning points:** Defense-in-depth ownership; caller-owned Session contracts;
Service transaction orchestration.

**Stop boundary:** Stop after the non-HTTP create core. Do not expose routes or
implement list/update/complete/delete use cases.

### Task 7.4 — Create and retrieve Task HTTP endpoints

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** not required until
Task 7.9.

**Goal:** Expose authenticated Task creation and owner-scoped detail retrieval.

**Prerequisites:** Tasks 7.1–7.3 are accepted and committed; no Task route exists.

**Files:** Add the project-consistent Task endpoint module, include it in the v1
Router, minimally update strict route tests, and add focused Task API tests.

**Implementation scope:** Add `POST /api/v1/tasks` returning 201 `PublicTask` and
`GET /api/v1/tasks/{task_id}` returning 200. Both reuse the Bearer dependency and
the same request-scoped synchronous Session. Router delegates to Services and
maps missing/foreign Task or Project to fixed safe 404 responses. Malformed UUID
and strict request errors remain 422.

**Explicitly not included:** No list, PATCH, complete, reopen, DELETE, tags,
Project route changes, or Agent endpoint.

**Automated tests:** Cover thin delegation, trusted `current_user.id`, same
Session, 201/200 allowlists, 401 challenge, 404 isolation, 422, forbidden
`user_id`/status/completed timestamp, exact OpenAPI methods, and Stage 6 route
compatibility.

**Real validation commands:** Run Task API focused and strict main/OpenAPI tests,
then affected authentication/Project regression, Ruff, format, mypy, lock, diff.

**Acceptance criteria:** Public authenticated create/detail routes match the
fixed contract and cannot reveal or accept ownership internals.

**Learning points:** Bearer dependency reuse; Router/Service error mapping;
response allowlists.

**Stop boundary:** Stop after create and detail. Do not add list, update,
completion, or deletion routes.

### Task 7.5 — Stable pagination, filters, overdue query, and sort allowlist

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** final query proof in
Task 7.9.

**Goal:** Add bounded owner-scoped Task listing with deterministic query behavior.

**Prerequisites:** Task 7.4 is accepted; query Schema is fixed.

**Files:** Extend existing Task Repository, Service, endpoint, and their focused
tests; do not create parallel implementations.

**Implementation scope:** Add `GET /api/v1/tasks` using the fixed page, filters,
overdue definition, allowlisted sort fields/direction, explicit null ordering,
and ID tie-breaker. List and count statements share identical owner/filter
predicates. The Service injects an aware UTC `now` for deterministic overdue
tests and builds exact page metadata.

**Explicitly not included:** No arbitrary SQL sort, client `user_id`, update,
completion, deletion, full-text engine, or Agent Tool.

**Automated tests:** Prove compiled owner predicates; every filter alone and in
combination; title escaping/search behavior; overdue boundaries; range rejection;
stable sort/tie-breaker/null order; page totals; no duplicates/omissions; 401;
strict query parameters; and public allowlists.

**Real validation commands:** Run focused Task repository/service/API listing
tests and Project pagination regression, then Ruff, format, mypy, lock, diff.

**Acceptance criteria:** Listing is bounded, deterministic, owner-isolated, and
has no route from user input to arbitrary SQL identifiers.

**Learning points:** Stable database pagination; allowlisted dynamic ordering;
clock injection for overdue rules.

**Stop boundary:** Stop after read/list behavior. Do not implement PATCH,
completion, reopen, or DELETE.

### Task 7.6 — Field updates and deadline invariants

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** final proof in Task
7.9.

**Goal:** Add strict owner-scoped Task field updates while preserving date and
transaction invariants.

**Prerequisites:** Tasks 7.1–7.5 are accepted; update Schema semantics are fixed.

**Files:** Extend existing Task Repository, Service, endpoint, exceptions, and
focused tests.

**Implementation scope:** Add `PATCH /api/v1/tasks/{task_id}`. Combine supplied
fields with persisted state before checking planned/due order. Allow clearing
description, planned date, due instant, and estimate; require non-null title,
priority, and status. Handle non-completed transitions now; preserve
`completed_at` rules for Task 7.7. Actual writes advance UTC `updated_at` and
commit once; no-op returns without write/commit; write failures roll back.

**Explicitly not included:** No direct `COMPLETED`, completion timestamp supplied
by clients, delete/archive, optimistic locking, or new migration.

**Automated tests:** Cover every editable field; explicit null/missing; combined
persisted dates; boundaries; owner/missing 404; invalid transition/input 422 or
documented safe domain error; actual commit/timestamp; no-op neutrality; rollback;
same Session; and OpenAPI contract.

**Real validation commands:** Run Task update Schema/Service/API focused tests and
Project update regression, then full ordinary pytest, Ruff, format, mypy, lock,
and diff.

**Acceptance criteria:** Only owned Tasks change; post-update state always
satisfies dates and non-completed lifecycle rules; transaction ownership remains
in the Service.

**Learning points:** Persisted-plus-patch validation; idempotent write avoidance;
safe domain errors versus HTTP mapping.

**Stop boundary:** Stop after ordinary field/deadline updates. Do not add the
completion action, deletion, PostgreSQL final suite, or Agent code.

### Task 7.7 — Idempotent completion and reopen transitions

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** final proof in Task
7.9.

**Goal:** Complete the Task state machine with server-owned completion time.

**Prerequisites:** Task 7.6 is accepted; ordinary PATCH and clocks are testable.

**Files:** Extend the existing Task Service/endpoint/schema validation and
focused lifecycle tests; add no parallel state-machine module without need.

**Implementation scope:** Add `POST /api/v1/tasks/{task_id}/complete`. First
completion assigns an injected aware UTC clock, sets `COMPLETED`, advances
`updated_at`, and commits once. Repeated completion performs no write, clock read,
or commit. Extend PATCH so a completed Task may move to TODO, IN_PROGRESS, or
CANCELLED while atomically clearing `completed_at`; repeated status values remain
no-ops. Database checks remain the final status/timestamp defense.

**Explicitly not included:** No client `completed_at`, bulk completion, recurrence,
study-duration accounting, notifications, or Agent execution.

**Automated tests:** Cover every allowed transition, rejected direct completed
PATCH, first/repeated completion, reopen timestamp clearing, no-op clocks,
commit/rollback, owner 404, 401, response fields, OpenAPI, and failure safety.

**Real validation commands:** Run lifecycle Service/API focused tests and update
regression, then Ruff, format, mypy, lock, and diff.

**Acceptance criteria:** `completed_at` and status cannot disagree through public
use cases; repeated operations are demonstrably transaction-neutral.

**Learning points:** Explicit state machines; server-owned timestamps; semantic
idempotency.

**Stop boundary:** Stop after completion/reopen behavior. Do not implement DELETE
or final PostgreSQL/documentation work.

### Task 7.8 — Owner-scoped safe hard deletion

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** final isolation proof
in Task 7.9.

**Goal:** Implement the requirements-defined Task hard deletion without resource
enumeration or hidden soft-delete semantics.

**Prerequisites:** Task 7.7 is accepted; ownership lookup and write transactions
are stable.

**Files:** Extend the existing Task Repository, Service, endpoint, strict route
tests, and focused delete tests.

**Implementation scope:** Add `DELETE /api/v1/tasks/{task_id}` returning 204 and
no body. Repository locates/deletes only `(task_id, user_id)` and flushes without
commit. Service commits successful deletion and rolls back failures. Missing and
foreign-owned Tasks return the same safe 404. Deletion removes only the Task;
there are no Stage 7 dependent records.

**Explicitly not included:** No archive flag, soft delete, restore, cascade into
future records, batch delete, Project delete, approval flow, or Agent Tool.

**Automated tests:** Cover 204/no body, exact owner predicate, missing/foreign
404 equality, malformed ID, 401, success commit, failure rollback, Repository
transaction neutrality, record removal, strict OpenAPI method, and absence of
archive/restore paths.

**Real validation commands:** Run focused delete Repository/Service/API tests and
all Task route tests, then full ordinary pytest, Ruff, format, mypy, lock, diff.

**Acceptance criteria:** An authenticated user can permanently delete only their
own Task; no response or route reveals foreign ownership; no soft-delete state
exists.

**Learning points:** HTTP 204 semantics; secure hard deletion; ownership-scoped
destructive writes.

**Stop boundary:** Stop after Task DELETE. Do not start integration documentation,
Stage 8 tools, or any bulk/high-impact delete workflow.

### Task 7.9 — Real PostgreSQL isolation/invariant integration and documentation

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** required.

**Goal:** Prove the complete Task API, ownership, state machine, queries,
transactions, database invariants, migration round trip, and operator contract.

**Prerequisites:** Tasks 7.1–7.8 are accepted; ordinary tests pass; exactly one
Task migration exists; no unresolved Task defect remains.

**Files:** Add narrowly scoped `tests/integration/test_tasks.py`, minimally extend
safe integration fixtures only when necessary, and update README Stage 7 usage.
Normative docs change only to correct a demonstrated conflict.

**Implementation scope:** Through real Bearer HTTP requests and independent
synchronous Sessions, prove create/detail/list/filter/sort/update/complete/reopen/
delete, two-user isolation, no-op behavior, exact cleanup, and safe errors. Use
direct controlled writes to prove every named Task constraint and the composite
Project ownership key. From an empty guarded `postgres-test`, upgrade to the Task
head, downgrade to `4d8c7a1b2e90` while retaining Users/Projects, re-upgrade,
inspect objects, and run `alembic check`. Document fields, routes, filters, state
transitions, deletion, transactions, ownership, and explicit omissions.

**Explicitly not included:** No new migration, schema redesign, SQLite substitute,
tags, study sessions, recurrence, collaboration, reminders, Stage 8 dependency,
LLM call, Tool, or Agent code.

**Automated tests:** Cover all public success/failure/boundary paths; two users
and two Projects; stable multi-page results; every filter/sort; due/overdue clock;
status/completed invariants; first/repeated completion; reopen; delete; exact
cleanup; Session closure; named database constraint diagnostics kept out of API;
and migration structure/round trip.

**Real validation commands:** Run locked sync, focused Task ordinary tests, full
ordinary and warnings suites; safely start only `postgres-test`; validate its
identity; run migration round trip, focused Task integration, full integration,
`alembic current/heads/check`; then Ruff, format, mypy, lock, diff, and stop only
`postgres-test` without deleting volumes.

**Acceptance criteria:** All Task operations traverse Router -> Service ->
Repository -> real PostgreSQL; cross-user data is unobservable; constraints and
transactions hold; public output has no internal owner or sensitive data; docs
match OpenAPI; the Task migration is reversible and drift-free.

**Learning points:** End-to-end ownership proof; state-machine/database invariant
alignment; migration and documentation as executable contracts.

**Stop boundary:** Task 7.9 completes Stage 7. Stop for owner confirmation. Do
not begin Stage 8 provider settings, model SDK, Agent tools, or empty Agent trees.

Do not add tags, study sessions, recurrence, collaboration, reminders, or Agent
execution before this domain API is stable. Stage 7 deliberately ends with hard
deletion rather than archive/restore because the accepted requirements defer
soft deletion until a demonstrated recovery need exists.

### Stage 8 — LLM foundation and Agent tools

Build a transparent, replaceable, and testable minimum Agent foundation before
LangGraph. The bounded path is:

```text
Validated goal
    -> versioned prompt
    -> replaceable model provider
    -> strict structured output
    -> allowlisted Agent tool
    -> existing Domain Service
    -> owner-scoped Repository
    -> PostgreSQL
    -> bounded public result
```

Stage 8 is split into three reviewable result milestones: Tasks 8.1–8.3 produce
one strict structured study plan through one verified provider adapter; Tasks
8.4–8.5 expose independently testable read/write tools backed by existing
Services; Tasks 8.6–8.8 combine those pieces into a bounded internal loop with
safe progress events and metrics. Each Task retains its own focused tests and
stop boundary.

Task 8.1 must select the provider and maintained SDK only after checking official
documentation for Python 3.14 support, current maintenance, structured output,
tool calling, streaming, timeouts, retries, and usage metadata. If the chosen SDK
cannot meet the required contract, stop and report the evidence rather than
installing it. Implement one narrow adapter and one Protocol, not a framework for
hypothetical providers.

Tool schemas never contain `user_id`; a trusted runtime context injects identity
outside model-controlled arguments. Tools do not receive Sessions, import
repositories, or own transactions. Agent runtime state and events contain only
serializable values, never Sessions, connections, ORM objects, provider clients,
keys, complete prompts, hidden reasoning, or complete sensitive tool payloads.
Ordinary tests use deterministic fakes and never require network access or an API
key. External-provider tests are separately marked and explicitly opted into.
Stage 8 keeps the synchronous SQLAlchemy architecture and creates no migration.

### Task 8.1 — Secret-aware provider settings and one replaceable adapter

**Estimated time:** 1–2 focused hours. **Network:** required only for official
documentation/package resolution during implementation. **External credential:**
not required for ordinary acceptance. **Real PostgreSQL:** not required.

**Goal:** Establish one evidence-selected model SDK behind a minimal replaceable
interface, with fail-fast secret-aware configuration and no provider details in
the rest of the application.

**Prerequisites:** Stage 7 is accepted; the worktree is clean; Python 3.14 and the
locked toolchain work; no Agent package or model dependency already exists.

**Files:** `pyproject.toml`, `uv.lock`, `.env.example`, `app/core/config.py`, new
`app/agent/__init__.py`, new `app/agent/providers.py`, and focused
`tests/test_agent_config.py` and `tests/test_agent_provider.py`. Add no empty
Agent subpackages.

**Implementation scope:** Record the provider-selection evidence in the Task
report; add only the selected official/maintained SDK. Add `STMS_MODEL_PROVIDER`,
`STMS_MODEL_NAME`, and `STMS_MODEL_API_KEY` settings, with the key represented by
`SecretStr`, no default key, non-blank bounded provider/model names, and hidden
input in validation errors. Define typed serializable request/response and usage
records plus one small provider Protocol. Implement one adapter that translates
those records to the selected SDK and supports the structured-output, tool-call,
streaming, timeout, and usage capabilities needed by later Tasks without exposing
SDK objects beyond the adapter. Construction fails safely when required settings
are absent; importing the ordinary application must not require a key.

**Interfaces and contracts:** Provider input contains a prompt version, bounded
messages/instructions, an optional strict output schema, an allowlisted tool
catalog, and timeout metadata. Provider output contains validated text or tool
calls plus normalized usage data; it never contains credentials. The Protocol is
the only dependency consumed by Stage 8 orchestration and is implementable by a
deterministic fake.

**Explicitly not included:** No second provider, routing/fallback framework,
prompt templates, model call from an HTTP route, tool implementation, Agent loop,
LangGraph, AsyncSession, persistence, streaming endpoint, or real-key test.

**Automated tests:** Cover defaults and bounds, absent/short/blank settings,
SecretStr masking in `repr`, `str`, and validation errors, lazy construction,
adapter request/response normalization with an SDK fake, usage normalization,
SDK exception redaction, and proof that ordinary tests/imports make no network
call and need no key.

**Focused validation commands:** Run the provider/config focused tests, `uv lock
--check`, Ruff, format check, mypy, and `git diff --check`. Do not start Docker.

**Acceptance criteria:** One compatible dependency is locked without unrelated
upgrades; secret/model settings fail safely; provider-specific types stop at the
adapter; a deterministic fake satisfies the same Protocol; no secret or complete
model payload appears in output or errors.

**Learning points:** Dependency selection from primary evidence; secret-aware
configuration; ports/adapters as a test seam.

**Stop boundary:** Stop after settings, Protocol, and one adapter. Do not add
prompts, planning schemas, actual model orchestration, tools, or LangGraph.

### Task 8.2 — Versioned prompts and strict goal/plan/result schemas

**Estimated time:** 1–2 focused hours. **Network/external credential/real
PostgreSQL:** not required.

**Goal:** Define the complete validated boundary for a study-planning request and
its model-produced plan before any orchestration calls a provider.

**Prerequisites:** Task 8.1 is accepted; its Protocol is stable; no real provider
call is required by tests.

**Files:** New `app/agent/prompts.py`, new `app/agent/schemas.py`, focused
`tests/test_agent_prompts.py` and `tests/test_agent_schemas.py`, and only necessary
exports in `app/agent/__init__.py`.

**Implementation scope:** Add an immutable prompt identifier such as
`study-plan.v1` and a deterministic builder that separates trusted instructions
from the validated user goal. Define strict Pydantic 2 models with
`extra="forbid"` and hidden inputs: `PlanningGoal` with one trimmed objective of
1–2000 characters and at most 20 constraints of 1–500 characters;
`StudyPlanStep` with a stable bounded step key, position, title, description, and
success criteria; `StudyPlan` with a safe summary and 1–20 uniquely positioned
steps; and `PlanningResult` with prompt version, status, and the plan. Normalize
only documented text whitespace; do not interpret model prose as code or policy.

**Interfaces and contracts:** Prompts contain the version and the exact JSON
schema contract, ask only for a user-owned learning plan, and prohibit fabricated
database facts or hidden reasoning. Results are serializable Pydantic values and
carry no provider client, ORM object, Session, key, raw token, or chain-of-thought.

**Explicitly not included:** No provider call, tool name/arguments, project/task
write, prompt persistence, conversation memory, LangGraph state, RAG context,
approval, HTTP/CLI endpoint, or database change.

**Automated tests:** Cover every length/count boundary; trimming and blank
rejection; extra fields; duplicate/missing/out-of-order positions; timezone- and
JSON-safe serialization where applicable; prompt version stability; deterministic
prompt building; hostile text remaining untrusted data; and absence of secrets,
tool arguments, hidden reasoning, or internal objects from dumps and errors.

**Focused validation commands:** Run the prompt/schema focused tests, then Ruff,
format check, mypy, lock check, and diff check. Do not start Docker or call a
provider.

**Acceptance criteria:** The same validated goal always builds the same versioned
prompt; malformed or oversized plans fail closed; a valid result round-trips
through JSON; all public fields are explicit allowlists.

**Learning points:** Structured output as an API boundary; prompt versioning;
untrusted user/model text handling.

**Stop boundary:** Stop after pure prompts and schemas. Do not call the adapter,
add retry logic, tool schemas, an Agent loop, or LangGraph.

### Task 8.3 — Structured planning with timeout, retry, and safe errors

**Estimated time:** 1–2 focused hours. **Network/external credential/real
PostgreSQL:** not required for acceptance.

**Goal:** Produce one strict `PlanningResult` through the provider Protocol with
deterministic retry bounds and safe application-level failures.

**Prerequisites:** Tasks 8.1–8.2 are accepted; the provider port, fake, prompt,
and schemas are stable.

**Files:** New `app/agent/planning.py`, extend `app/agent/providers.py` and
`app/core/exceptions.py` only as required, plus focused
`tests/test_agent_planning.py` and provider regression tests.

**Implementation scope:** Add a pure orchestration function/service that validates
the goal, builds the versioned prompt, requests the exact `PlanningResult` schema,
and returns only the validated result. Use a 30-second per-attempt timeout and at
most two total attempts. Retry once only for a timeout, explicitly classified
transient provider failure, or invalid structured response; do not retry missing
configuration, authentication/permission failures, or deterministic input errors.
Map exhausted/unsafe provider failures to stable local exception classes with
fixed messages that omit prompts, raw responses, SDK diagnostics, keys, and
complete model output.

**Interfaces and contracts:** Clock/sleeper/provider dependencies are injectable;
ordinary tests remain instantaneous and offline. Retry count and timeout are
bounded constants for this Task, not user-controlled inputs. A failed attempt
cannot return partial model data as a valid plan.

**Explicitly not included:** No fallback provider, unbounded exponential retry,
tool call, Session, database access, Router, CLI, streaming, metrics persistence,
LangGraph, or external-provider acceptance requirement.

**Automated tests:** Cover first-attempt success; one transient/timeout/schema
retry then success; exhaustion after exactly two attempts; non-retryable failure;
invalid goal before provider use; exact prompt/schema/version forwarding; fake
call counts; timeout forwarding; safe exceptions; and absence of keys, prompts,
raw responses, complete model output, or hidden reasoning in logs/errors.

**Focused validation commands:** Run Tasks 8.1–8.3 focused tests, then the full
ordinary suite, Ruff, format check, mypy, lock check, and diff check. No Docker or
external model call is required.

**Acceptance criteria:** A deterministic fake proves a validated goal can become
one strict versioned plan; all calls terminate within exact attempt bounds; unsafe
or malformed provider output fails closed without secret disclosure.

**Learning points:** Bounded resilience; exception classification; validation at
an untrusted model boundary.

**Stop boundary:** Tasks 8.1–8.3 form the first Stage 8 result milestone. Stop for
review; do not add tools, database access, a loop, streaming, or LangGraph.

### Task 8.4 — Service-backed read tools for Projects and Tasks

**Estimated time:** 1–2 focused hours. **External model/network:** not required.
**Real PostgreSQL:** deferred to the Stage 8 final verification unless a focused
test demonstrates a database-specific defect.

**Goal:** Expose `list_projects` and `list_tasks` as strict, bounded Agent tools
that reuse existing owner-scoped Domain Services without giving the model an
identity, Session, or persistence primitive.

**Prerequisites:** Task 8.3 is accepted; Stage 6/7 list Schemas and Services are
stable; trusted runtime identity semantics are understood.

**Files:** New `app/agent/context.py` and `app/agent/tools.py`, new
`app/services/agent_domain.py` only for the narrow runtime-to-Service boundary,
focused `tests/test_agent_read_tools.py`, and necessary Agent exports.

**Implementation scope:** Define an immutable trusted runtime context containing
the authenticated `user_id` outside all model schemas. Define strict read-tool
arguments that reuse the existing pagination/filter bounds but contain no
`user_id`, Session, repository, SQL, or arbitrary sort field. A small domain
service gateway creates/closes a synchronous Session through the existing factory
and delegates to `list_owned_projects` or `list_owned_tasks`; tools invoke only
that gateway and return bounded public-schema data. Read calls do not commit.

**Interfaces and contracts:** The allowlist has exactly `list_projects` and
`list_tasks`. Tool descriptions and JSON schemas are deterministic and safe to
send to a model. Runtime context is injected by trusted application code and is
not serialized into tool arguments or Agent state.

**Explicitly not included:** No create/update/delete/complete/archive Tool,
repository import from a Tool, caller/model-selected owner, raw ORM output,
provider call, public Agent route, LangGraph, RAG, approval, or AsyncSession.

**Automated tests:** Cover exact tool names/schemas; absence and rejection of
`user_id`/extra fields; pagination/filter boundaries; trusted identity forwarding;
existing Service invocation; public field allowlists; cross-user isolation via
Service fakes; Session creation/closure; no commit on reads; safe domain errors;
and static import-boundary assertions that Tools do not import repositories or
SQLAlchemy.

**Focused validation commands:** Run read-tool, Project list, Task list, and
Session focused tests, then Ruff, format check, mypy, lock, and diff. Ordinary
acceptance uses no provider or Docker.

**Acceptance criteria:** The model can request only bounded Project/Task reads;
trusted identity is always injected; Tool code cannot access a Session or
repository; outputs match existing public contracts.

**Learning points:** Capability-safe tool schemas; trusted context injection;
reusing domain authorization across entry points.

**Stop boundary:** Stop after the two read tools. Do not add write tools, a loop,
public endpoint, approval flow, or LangGraph.

### Task 8.5 — Service-backed write tools for Task creation and update

**Estimated time:** 1–2 focused hours. **External model/network:** not required.
**Real PostgreSQL:** final proof may reuse the guarded Stage 7 database suite.

**Goal:** Expose only `create_task` and `update_task` through the same existing
Task Service rules and transactions while keeping ownership outside model input.

**Prerequisites:** Task 8.4 is accepted; read-tool context/gateway boundaries are
stable; existing Task create/update behavior is fully tested.

**Files:** Extend `app/agent/tools.py`, `app/services/agent_domain.py`, and Agent
exports; add focused `tests/test_agent_write_tools.py` and minimal read-tool/Task
Service regression adjustments only when contracts require them.

**Implementation scope:** Add strict arguments equivalent to the existing
`TaskCreate` and `TaskUpdate` public edit fields, plus `task_id` for update, but
never `user_id`, Session, transaction flags, timestamps owned by the server, or
arbitrary fields. The runtime gateway opens/closes one synchronous Session and
calls `create_task` or `update_owned_task` with trusted identity. Existing
Services retain commit/rollback and ownership/date/state rules; Tools only
validate, delegate, and return `PublicTask`. Unknown tools and invalid arguments
fail before a Session is opened.

**Interfaces and contracts:** The complete Stage 8 allowlist is now exactly
`list_projects`, `list_tasks`, `create_task`, and `update_task`. Writes are only
available when trusted runtime policy enables them; model output alone never
grants authority. Durable human approval/idempotency arrives in Stage 10.

**Explicitly not included:** No delete, complete action, bulk write, Project
write, direct commit/rollback in Tools or repositories, arbitrary `user_id`,
approval persistence, retrying writes, Agent HTTP route, LangGraph, or migration.

**Automated tests:** Cover exact write schemas/allowlist; forbidden owner/internal
fields; create/update success; validation/date/state failures; foreign/missing
404 equivalence; trusted identity forwarding; one Session per call and guaranteed
closure; Service-owned commit/rollback; no Tool retry after a write failure; safe
public output; and proof that Tools import neither repositories nor SQLAlchemy.

**Focused validation commands:** Run write/read-tool tests plus Task
Schema/Service/ownership regressions, then the full ordinary suite, Ruff, format,
mypy, lock, and diff. Use guarded `postgres-test` only if the accepted Task calls
for real database proof; never use an external model.

**Acceptance criteria:** All four allowlisted Tools are independently executable
with a fake runtime; writes traverse Tool -> existing Domain Service -> Repository
-> PostgreSQL contract; no model-controlled value can change ownership or
transaction policy.

**Learning points:** Command validation; transaction reuse across entry points;
least-authority write capabilities.

**Stop boundary:** Tasks 8.4–8.5 form the second Stage 8 result milestone. Stop
for review; do not build the model loop, autonomous retries, streaming, approval,
or LangGraph.

### Task 8.6 — Minimal bounded tool-calling loop and internal test entry

**Estimated time:** 1–2 focused hours. **Network/external credential/real
PostgreSQL:** not required for ordinary acceptance.

**Goal:** Combine the provider port, strict plan contracts, and four allowlisted
tools into one deterministic, terminating internal loop without introducing a
public Agent API or LangGraph.

**Prerequisites:** Tasks 8.1–8.5 are accepted; provider and tool fakes are stable;
trusted identity is supplied by runtime code outside model arguments.

**Files:** New `app/agent/loop.py`, a test-only/internal entry in
`app/agent/testing.py`, necessary Agent exports, and focused
`tests/test_agent_loop.py`. Do not create a CLI accepting an arbitrary `user_id`;
the internal test entry is the accepted Stage 8 host boundary.

**Implementation scope:** Execute at most four model rounds, no more than three
tool calls from one model response, and no more than eight tool calls total.
Validate every provider result, tool name, and argument schema before dispatch.
Inject trusted runtime context separately, feed only bounded safe tool results
back to the provider, and return a strict `PlanningResult` or stable safe failure.
Unknown tools, duplicate/excess calls, invalid arguments, provider exhaustion,
or missing final result terminate the run; no `sleep`, recursion, or unbounded
self-correction is allowed.

**Interfaces and contracts:** Loop input is the validated goal plus trusted
runtime context and injected provider/tool registry. Loop output and intermediate
state are JSON-serializable validated records. A tool failure is not retried by
the loop, especially after a possible write.

**Explicitly not included:** No public Router, arbitrary CLI owner input,
conversation memory, parallel tool calls, write retry, durable run state,
approval/interrupt, LangGraph, Checkpoint, SSE, RAG, MCP, or multi-agent behavior.

**Automated tests:** Cover no-tool plan success; read then plan; allowed write;
unknown/invalid/excess tool calls; exact round/per-response/total limits; provider
and tool failures; no retry after writes; trusted identity isolation; deterministic
fake transcripts; serializable state/output; and absence of keys, complete
prompts/model responses, hidden reasoning, or sensitive tool payloads in errors.

**Focused validation commands:** Run loop, planning, and all Tool focused tests,
then Ruff, format, mypy, lock, and diff. No Docker or real provider is required.

**Acceptance criteria:** A fake provider can drive one useful bounded plan/tool
scenario; every branch terminates within fixed limits; only the exact allowlist is
dispatchable; the model cannot select identity or database primitives.

**Learning points:** Bounded Agent execution; deterministic dispatch; separating
model suggestions from trusted authority.

**Stop boundary:** Stop after the internal loop. Do not add streaming events,
metrics, public API, persistence, approval, or LangGraph.

### Task 8.7 — Streaming foundation and safe progress events

**Estimated time:** 1–2 focused hours. **External credential/network/real
PostgreSQL:** not required for acceptance.

**Goal:** Represent incremental provider/loop progress as a safe, ordered event
stream without exposing hidden reasoning or committing to Stage 10 SSE transport.

**Prerequisites:** Task 8.6 is accepted; the selected adapter's official streaming
API and cancellation semantics were verified in Task 8.1.

**Files:** New `app/agent/events.py`, extend `app/agent/providers.py` and
`app/agent/loop.py` only as necessary, focused `tests/test_agent_events.py` and
streaming regression in `tests/test_agent_loop.py`/`tests/test_agent_provider.py`.

**Implementation scope:** Define strict serializable progress events with
monotonic sequence, aware UTC timestamp, allowlisted event kind, stage/tool name
when applicable, bounded safe summary, outcome, and optional normalized usage.
Translate provider chunks and loop transitions into events while accumulating
the same final validated result as the non-streaming path. Redact or reject raw
prompt/model payloads, complete tool arguments/results, tokens, credentials, and
chain-of-thought. Ensure cancellation/error closes the provider stream and emits
at most one terminal safe event.

**Interfaces and contracts:** Event kinds are a fixed enum such as `run_started`,
`model_started`, `tool_started`, `tool_finished`, `result_ready`, and `run_failed`.
Transport-neutral iteration is the boundary; Stage 10 maps it to SSE. Consumers
cannot infer authorization from an event.

**Explicitly not included:** No FastAPI streaming response, SSE wire format,
WebSocket, background worker, checkpoint, resume, persistent trace, approval,
hidden reasoning, LangGraph, or real-provider ordinary test.

**Automated tests:** Cover deterministic order/sequence/time; chunk accumulation;
same final result as non-streaming; tool progress summaries; bounds/redaction;
cancellation and provider/tool errors; exactly one terminal event; JSON round
trip; and no secrets, complete tokens, prompts, tool payloads, or chain-of-thought.

**Focused validation commands:** Run event/stream/provider/loop focused tests,
then Ruff, format, mypy, lock, and diff. No Docker or external key is required.

**Acceptance criteria:** Deterministic fakes produce safe ordered events and one
validated final result; cancellation releases resources; the event contract can
later be carried over SSE without exposing model internals.

**Learning points:** Streaming as domain events versus transport; cancellation
cleanup; observability without reasoning disclosure.

**Stop boundary:** Stop after transport-neutral progress streaming. Do not add an
HTTP stream, durable runs/checkpoints, approval/resume, or LangGraph.

### Task 8.8 — Metrics, deterministic fakes, and external-provider smoke boundary

**Estimated time:** 1–2 focused hours. **External credential/network:** optional
and explicitly opt-in only. **Real PostgreSQL:** required only for existing
service-backed Tool integration regressions, not for model calls.

**Goal:** Complete Stage 8 with normalized token/latency evidence, reusable fakes,
prompt-version reporting, and a safe separately marked provider smoke test.

**Prerequisites:** Tasks 8.1–8.7 are accepted; no unresolved provider/tool/loop
defect remains; ordinary tests remain completely offline.

**Files:** New `app/agent/metrics.py`, minimal extensions to Agent result/events,
new `tests/fakes/agent_provider.py`, focused `tests/test_agent_metrics.py`, new
`tests/external/test_agent_provider_smoke.py`, `pyproject.toml` only for the
explicit external marker, and `.env.example`/README only to document already
implemented non-secret provider settings and opt-in commands.

**Implementation scope:** Normalize per-call and per-run input/output/total token
counts when the provider supplies them, monotonic latency, attempt count, tool
count, outcome, and prompt version. Missing provider usage remains explicit
`None`, never fabricated. Aggregate only safe numeric/enum/version data. Provide
a scriptable fake with fixed responses, tool calls, chunks, failures, and usage.
Register an `external_provider` pytest marker excluded from default runs; its one
smoke test skips safely without explicit credentials and validates only a tiny
structured response when deliberately enabled. Document that external calls may
cost money and must use a synthetic prompt.

**Interfaces and contracts:** Metrics are immutable serializable values and do
not contain goal text, prompts, model responses, tool arguments/results, identity,
keys, tokens, or hidden reasoning. Ordinary CI/default pytest never selects the
external marker and never reads a real model key.

**Explicitly not included:** No metrics database, tracing vendor, budget billing,
benchmark claims, automatic external call, provider fallback, public Agent API,
LangGraph, Checkpoint, HITL, RAG, MCP, multi-agent, or Stage 9 code.

**Automated tests:** Cover exact token aggregation, missing usage, monotonic fake
latency, retry/tool counts, prompt version, success/failure outcomes, serialization
and redaction, reusable fake behaviors, default external-test exclusion, no-key
safe skip, and SDK-adapter smoke contract behind explicit marker. Regress all
Tasks 8.1–8.7 with no network.

**Real validation commands:** Run all Stage 8 focused tests; affected Stage 6/7
Service regressions; full ordinary and warnings suites; Ruff, format, mypy, lock,
and diff checks. Verify the default test selection excludes `external_provider`.
Only when the owner explicitly authorizes credentials/cost may the separately
marked smoke test run. If Tool integration is required, use only guarded
`postgres-test` and stop it without deleting volumes.

**Acceptance criteria:** Stage 8 demonstrates an offline fake-driven path from a
validated goal through versioned structured planning and allowlisted
Service-backed Tools to a bounded result, safe events, and metrics. The optional
provider smoke boundary is isolated, no ordinary test needs a key/network, and no
secret/sensitive payload is recorded.

**Learning points:** Useful Agent metrics versus sensitive traces; deterministic
test doubles; separating optional external verification from CI.

**Stop boundary:** Task 8.8 completes Stage 8. Stop for owner confirmation. Do
not begin LangGraph/Stage 9, persistence/approval/Stage 10, RAG, MCP, multi-agent,
or any Stage 5 deferred authentication work.

### Stage 9 — Single-Agent LangGraph workflow

Introduce LangGraph only after the independently executable Stage 8 provider,
schemas, tools, bounded loop, events, and metrics are accepted. This stage builds
one explicit workflow; it does not add another persistence or authorization
stack. The dependency direction remains `LangGraph node -> Agent tool -> Domain
service -> Repository -> PostgreSQL`.

The graph carries only strict JSON-serializable state. Authenticated identity,
provider objects, the Tool gateway, clocks, and an in-process approval adapter are
trusted runtime dependencies outside state and outside model-visible schemas.
State may contain validated goals, safe analysis, bounded public Project/Task
context, a validated plan proposal, approval outcome, safe execution records,
verification, public summary, counters, and stable local error codes. It never
contains a Session, connection, transaction, ORM object, repository, service,
provider client, API key, access token, complete prompt/response, raw tool
payload, database diagnostic, or hidden reasoning.

Stage 9 deliberately has no checkpoint. Approval can be supplied by a controlled
in-process test/host adapter, and a bounded edit decision can revisit planning.
Durable pause/resume, service-restart recovery, business run/approval records,
official PostgreSQL checkpoints, stable `thread_id`/`run_id`, idempotency across
replay, and SSE transport remain Stage 10 responsibilities.

The result milestones are:

1. Tasks 9.1–9.3: a validated goal becomes a serializable, context-backed,
   deterministically validated plan proposal through independently tested nodes.
2. Tasks 9.4–9.5: a trusted in-process approval/edit decision gates a small,
   bounded set of existing Task write tools; rejected plans cause no writes.
3. Tasks 9.6–9.7: verification and public summary nodes are composed into one
   offline-tested LangGraph with bounded conditional branches and safe failures.

### Task 9.1 — State contract and graph input/output schemas

**Estimated time:** 1–2 focused hours. **Network/external provider/real
PostgreSQL:** not required. **New dependency:** none; LangGraph is not yet needed
to define pure state contracts.

**Goal:** Define the complete strict, bounded, serializable data contract that all
Stage 9 nodes exchange, without putting trusted runtime objects or identity under
model control.

**Prerequisites:** Stage 8 is accepted and committed; its `PlanningGoal`,
`PlanningResult`, public Project/Task schemas, tool allowlist, runtime context,
safe metrics, and error conventions are stable.

**Files:** New `app/agent/state.py`, focused `tests/test_agent_state.py`, and
`app/agent/__init__.py` only if the existing export style requires an explicit
public internal contract. Do not create graph or node modules yet.

**Implementation scope:** Add frozen Pydantic 2 contracts for the graph input,
state, and public output plus their supporting enums/records. The input contains
only one validated `PlanningGoal`; authenticated `user_id` remains solely in
`AgentRuntimeContext`. Define bounded values for: deterministic goal analysis;
the first bounded page of public Project and Task context; a plan proposal that
pairs the existing `PlanningResult` with zero to three proposed Task write
actions; approval decision and bounded edit feedback; revision count with a
maximum of two edits; safe action execution records; verification; terminal
outcome; and a concise public summary. Proposed actions have a unique bounded
action key, a tool name restricted to `create_task` or `update_task`, and strict
arguments later validated through the existing Tool contract. They cannot carry
`user_id`, Session, transaction options, timestamps owned by the server, or
arbitrary internal fields.

**Interfaces and contracts:** Node updates are partial mappings over one canonical
`AgentGraphState`; they may change only fields owned by that node. State and
output round-trip through JSON. The public `AgentGraphOutput` is an explicit
whitelist containing only terminal status, the accepted public plan when
available, a safe summary, safe execution records/public Task results as
specified by the accepted contract, and safe aggregate metrics/counters. It does
not dump the internal state.

**Explicitly not included:** No LangGraph import, Node, graph, Router, Session,
database access, model call, Tool execution, approval UI, checkpoint, run table,
SSE, or migration.

**Automated tests:** Cover valid minimal and fully populated state; exact fields;
extra-field rejection; all collection/text/count bounds; unique action keys;
write-tool-only action names; no model-supplied identity/internal fields; edit
limit; aware/JSON-safe values; immutable models; JSON round trips; explicit
public output whitelist; and dumps/errors that exclude secrets, complete tokens,
hidden reasoning, Sessions, ORM objects, prompts, and raw provider/tool payloads.

**Focused validation commands:** Run `tests/test_agent_state.py` plus existing
Agent schema/tool tests, then Ruff, format check, mypy, lock check, and diff
check. Do not start Docker or call a provider.

**Acceptance criteria:** Every later Node has one unambiguous serializable input
and output field contract; trusted identity and runtime dependencies cannot enter
model-controlled data; invalid or oversized state fails before orchestration.

**Learning points:** Reducer-safe state design; public output versus internal
workflow state; serializability as a recovery prerequisite.

**Stop boundary:** Stop after pure schemas. Do not add LangGraph, nodes, provider
calls, tools, approvals, graph composition, or persistence.

### Task 9.2 — `analyze_goal` and `load_context` nodes

**Estimated time:** 1–2 focused hours. **External provider/network/real
PostgreSQL:** not required; deterministic fakes prove the behavior.

**Goal:** Turn the validated goal into a transparent safe analysis and load the
bounded owner-scoped context needed for planning through existing read tools.

**Prerequisites:** Task 9.1 state contracts are accepted; the Stage 8 read Tool
schemas and trusted `AgentRuntimeContext` boundary remain unchanged.

**Files:** New `app/agent/nodes/__init__.py` and
`app/agent/nodes/context.py`, focused `tests/test_agent_context_nodes.py`, and
minimal state/schema adjustments only if a focused test exposes an omission in
Task 9.1.

**Implementation scope:** Implement two independently callable synchronous node
functions. `analyze_goal` deterministically validates/normalizes the existing
goal and records only a concise objective, bounded constraints, and an allowlist
of required context (`projects`, `tasks`); it does not invent facts or persist
chain-of-thought. The initial MVP loads both context kinds. `load_context` invokes
only `list_projects` and `list_tasks` through the existing `execute_tool` boundary
with fixed bounded first-page arguments and a read-only trusted runtime context.
It stores only existing `ProjectListResponse`/`TaskListResponse` public data.

**Interfaces and contracts:** Node dependencies are injected by trusted host
code, not stored in state. `load_context` never calls `AgentDomainGateway`, a
Service, repository, or Session directly. Model text cannot select identity,
enable writes, change pagination bounds, or choose a new tool. Repeated pure
analysis is deterministic; read context loading performs no commit.

**Explicitly not included:** No model-based hidden analysis, write Tool, plan
generation, approval, graph edge, LangGraph dependency, direct database call,
context persistence, RAG, or unbounded Project/Task loading.

**Automated tests:** Cover exact node-owned updates; deterministic analysis;
fixed bounded read arguments; both read tools and no write tools; trusted identity
forwarding outside arguments; public context fields; empty context; existing
owner-safe 404/error behavior; one Tool call per required context kind; no commit;
safe failure without payload echo; no mutation of input state; JSON-safe updates;
and static proof that node code imports neither SQLAlchemy nor repositories.

**Focused validation commands:** Run context-node, state, read-tool, Project-list,
and Task-list focused tests, then Ruff, format check, mypy, lock, and diff. Use no
Docker or external model.

**Acceptance criteria:** A validated goal produces a transparent bounded analysis
and owner-scoped public context using only the accepted Agent Tool boundary; no
trusted dependency is serialized into state.

**Learning points:** Node-local state updates; deterministic analysis versus
hidden reasoning; graph reuse of capability-safe Tools.

**Stop boundary:** Stop after the two context nodes. Do not generate a plan, add
write actions, approvals, conditional edges, LangGraph, or persistence.

### Task 9.3 — `generate_plan` and deterministic `validate_plan` nodes

**Estimated time:** 1–2 focused hours. **Real PostgreSQL:** not required.
**External provider:** forbidden for acceptance; use the reusable Stage 8 fake.

**Goal:** Produce a strict plan proposal from the validated goal/analysis/context
and deterministically reject malformed, unsafe, or non-executable proposals
before any approval or write.

**Prerequisites:** Tasks 9.1–9.2 are accepted; Stage 8 provider, prompt version,
bounded failure, Tool argument validation, and metrics contracts are stable.

**Files:** New `app/agent/nodes/planning.py`, focused
`tests/test_agent_planning_nodes.py`, and minimal extensions to
`app/agent/prompts.py`, `app/agent/planning.py`, or `app/agent/state.py` only when
needed to pass the accepted bounded context/proposal contract. Preserve every
Stage 8 public/internal behavior and regression test.

**Implementation scope:** `generate_plan` builds versioned provider input from
the validated goal plus the bounded public context snapshot, requests the exact
plan-proposal JSON schema, and uses the existing provider/error/timeout boundary
rather than raw SDK calls. It may propose at most three individual
`create_task`/`update_task` actions and cannot execute them. `validate_plan` is
pure deterministic code: revalidate `PlanningResult`, prompt version, step/action
bounds and unique keys; validate the entire proposed action batch with
`validate_tool_arguments` against a trusted write-capable context without
creating a gateway/Session; and return a stable validation result or safe local
error codes. Any invalid action rejects the whole proposal before approval.

**Interfaces and contracts:** Loaded Project/Task data remains untrusted context,
not instructions. Provider keys, requests, complete responses, hidden reasoning,
identity, and raw validation diagnostics never enter state or errors. Provider
attempts remain bounded and observable through the existing safe metrics. No
write occurs in either node.

**Explicitly not included:** No Tool dispatch, transaction, approval, automatic
self-correction, unbounded retry, graph edge, LangGraph dependency, public API,
checkpoint, or database write.

**Automated tests:** Cover valid proposal; zero-action informational plan; one to
three valid actions; fourth-action rejection; duplicate action keys; forbidden
read/unknown tools in the execution proposal; invalid/extra arguments; attempted
`user_id`/internal fields; missing context; wrong prompt version; malformed model
output; provider timeout/transient/auth failures; exact attempt bounds; no Tool or
Session creation during validation; deterministic validation; safe state updates;
metrics; JSON round trip; and no secrets/prompts/raw responses/tool payloads in
errors or records.

**Focused validation commands:** Run planning-node, state/context-node, Stage 8
planning/loop/provider/Tool focused tests, then Ruff, format check, mypy, lock,
and diff. Do not start Docker or use a real provider.

**Acceptance criteria:** An offline scripted provider can turn bounded public
context into one strict proposal; deterministic validation proves every planned
write is in the existing allowlist and argument contract; no action has executed.

**Learning points:** Model proposal versus deterministic acceptance; validating
commands before authority is granted; bounded context assembly.

**Stop boundary:** Tasks 9.1–9.3 form the first Stage 9 result milestone. Stop
for review; do not add approval, execute tools, compose a graph, install
LangGraph, or enter Stage 10.

### Task 9.4 — `request_approval` decision and bounded plan-edit loop

**Estimated time:** 1–2 focused hours. **Network/external provider/real
PostgreSQL:** not required.

**Goal:** Require an explicit trusted in-process decision before proposed writes
and define deterministic routing for approve, reject, or bounded plan revision.

**Prerequisites:** Task 9.3 is accepted; only a deterministically valid proposal
may enter approval.

**Files:** New `app/agent/nodes/approval.py` and
`app/agent/routing.py`, focused `tests/test_agent_approval_nodes.py`, and minimal
state contract adjustments only if the accepted decision shape requires them.

**Implementation scope:** Define a narrow synchronous `ApprovalDecider` Protocol
implemented by controlled host/test code. `request_approval` receives only a safe
plan summary and action names/count—not raw credentials or hidden reasoning—and
stores its strict decision. Allow `approved`, `rejected`, or `request_changes`;
change feedback is bounded, treated as untrusted data, and never grants tools or
identity. A routing function sends approval to execution, rejection to summary,
or change requests back to plan generation. Permit at most two revisions; the
third request fails closed with a stable local outcome. Each return to planning
must pass `validate_plan` again before another approval decision.

**Interfaces and contracts:** Approval is supplied outside model output. The
model cannot mark its own proposal approved. Rejection and exhausted revisions
execute no writes. Routing functions are pure and return only allowlisted route
enums/names suitable for later conditional edges.

**Explicitly not included:** No durable interrupt, resume token, HTTP approval
endpoint, UI, polling, checkpoint, business approval row, service-restart
recovery, write execution, LangGraph composition, or Stage 10 behavior.

**Automated tests:** Cover all three decisions; decision/feedback validation;
approval only after valid plan; exact safe payload sent to the decider; no model
self-approval; zero writes on reject/change; first and second revisions; third
revision rejection; revalidation requirement; deterministic route names; fake
decision sequences; serialization/redaction; and no user identity, action
arguments, secrets, complete prompts, responses, tokens, or hidden reasoning in
the approval request/state/error.

**Focused validation commands:** Run approval/routing, planning-node, and state
focused tests, then Ruff, format check, mypy, lock, and diff. No Docker, provider,
or database is required.

**Acceptance criteria:** Every proposed write is gated by a trusted explicit
decision; reject and exhausted-edit paths are safe and terminating; the edit loop
has an exact bound and cannot bypass deterministic revalidation.

**Learning points:** Human authority versus model suggestion; conditional route
purity; bounding iterative feedback.

**Stop boundary:** Stop after in-process approval and routing logic. Do not
execute actions, add LangGraph edges, persist approval, interrupt/resume, or add
an approval API.

### Task 9.5 — `execute_tasks` through approved tools with bounded steps

**Estimated time:** 1–2 focused hours. **External provider:** not required.
**Real PostgreSQL:** optional only if a focused defect requires proof; ordinary
acceptance uses the existing Tool/gateway fakes.

**Goal:** Execute only a deterministically valid and explicitly approved set of
individual Task writes through the existing capability-safe Agent Tool boundary.

**Prerequisites:** Task 9.4 is accepted; state proves current proposal validation
and approval, and trusted runtime policy independently enables write tools.

**Files:** New `app/agent/nodes/execution.py`, focused
`tests/test_agent_execution_node.py`, and minimal state/routing changes only when
required by the accepted execution-record contract.

**Implementation scope:** `execute_tasks` verifies approval and validation again,
then sequentially dispatches at most three proposed actions through existing
`execute_tool` with the separately injected `AgentRuntimeContext`. It supports
only `create_task` and `update_task`; it never supplies identity from state. Stop
at the first failure, never retry or parallelize a write, and record only bounded
safe action key/tool/outcome and accepted public `PublicTask` data. Existing
Services retain owner checks and one transaction per Tool call; the gateway owns
Session creation/closure. Earlier committed actions are not falsely presented as
rolled back if a later independent action fails.

**Interfaces and contracts:** Plan-wide in-process approval does not expand the
Tool allowlist. The complete action batch was validated before the first write,
and each action is revalidated immediately before dispatch. No delete, complete,
Project write, bulk primitive, caller-selected transaction, or automatic replay
is available. Durable idempotency and safe resume arrive in Stage 10.

**Explicitly not included:** No Repository/Session import in node code, cross-tool
atomic transaction, retry, compensation, delete, batch API, checkpoint,
idempotency table/key, public Router, LangGraph composition, or external model.

**Automated tests:** Cover one and three approved actions; zero-action plan;
trusted identity forwarding outside arguments; approval and validation required;
write-disabled runtime rejection; sequential order; exact allowlist; foreign or
missing resources retaining safe domain errors; first-failure stop; no retry;
accurate partial-success records; Service-owned commit/rollback; one closed
Session per dispatched Tool in gateway tests; public-field output; input state
immutability; and no sensitive arguments, identity, SQL, diagnostics, secrets, or
hidden reasoning in records/errors.

**Focused validation commands:** Run execution-node, approval, write/read Tool,
Task Service/ownership, and state tests, then the full ordinary suite, Ruff,
format check, mypy, lock, and diff. Start guarded `postgres-test` only if a real
database defect is demonstrated; never call an external provider.

**Acceptance criteria:** Only validated approved actions execute, through Tool ->
Domain Service -> owner-scoped Repository; every branch terminates, failures are
not retried, and records truthfully represent independently committed writes.

**Learning points:** Approval as a necessary but not sufficient capability;
partial success across use-case transactions; safe command execution records.

**Stop boundary:** Tasks 9.4–9.5 form the second Stage 9 result milestone. Stop
for review; do not add verification/summary, compile LangGraph, persist state,
add idempotency, or enter Stage 10.

### Task 9.6 — `verify_result` and `summarize` nodes

**Estimated time:** 1–2 focused hours. **Network/external provider/real
PostgreSQL:** not required.

**Goal:** Deterministically verify the graph's recorded outcome and produce one
strict public summary without exposing internal state or claiming work that did
not complete.

**Prerequisites:** Task 9.5 is accepted; execution records distinguish full,
partial, rejected, and failed paths.

**Files:** New `app/agent/nodes/finalization.py`, focused
`tests/test_agent_finalization_nodes.py`, and minimal state contract refinements
needed for the accepted verification/output whitelist.

**Implementation scope:** `verify_result` compares the valid approved action set
with ordered execution records, verifies action key/tool/result consistency, and
classifies no-action success, full success, partial failure, rejected, and safe
failure without querying the database or model. `summarize` deterministically
creates `AgentGraphOutput` from verified state, the accepted public plan, public
Task results, safe counters/metrics, and bounded fixed-format summaries. It does
not summarize hidden reasoning or raw errors and never turns failure into success.

**Interfaces and contracts:** Verification is based on validated records, not
free-form model claims. Public output is a strict whitelist and never exposes
analysis, approval feedback, action arguments, complete tool results beyond
accepted public schemas, identity, internal routes, provider data, or state dump.

**Explicitly not included:** No fresh model call, database read, compensation,
retry, tracing vendor, persistence, HTTP response/SSE, LangGraph composition,
checkpoint, or evaluation framework.

**Automated tests:** Cover no-action/full/partial/rejected/failed paths; record
count/order/action mismatch; duplicate records; safe metrics propagation; strict
output fields; truthful status; bounded summaries; deterministic repeated output;
JSON round trip; no database/provider/tool use; and redaction of identity,
arguments, prompts, responses, credentials, diagnostics, feedback, and hidden
reasoning.

**Focused validation commands:** Run finalization, execution, approval, state,
and metrics focused tests, then Ruff, format check, mypy, lock, and diff. No
Docker or external provider.

**Acceptance criteria:** All terminal paths produce one truthful validated public
output or a stable safe failure; verification cannot be overridden by model text;
internal workflow state is not serialized as the response.

**Learning points:** Verification independent of generation; truthful partial
failure reporting; anti-corruption output boundaries.

**Stop boundary:** Stop after independent finalization nodes. Do not install or
compile LangGraph, expose an API, persist results, or add Stage 10 behavior.

### Task 9.7 — Graph composition, conditional branches, failures, and tests

**Estimated time:** 1–2 focused hours. **External provider/real PostgreSQL:** not
required for acceptance. **New dependency:** the minimal maintained LangGraph
package is added here, because this is the first Task that imports it.

**Goal:** Compose the eight accepted Nodes into one deterministic, terminating,
offline-tested single-Agent LangGraph while preserving trusted runtime and Stage
10 boundaries.

**Prerequisites:** Tasks 9.1–9.6 and all independent node tests are accepted;
Stage 8 provider/Tool fakes remain green; no unresolved state or routing contract
exists.

**Files:** New `app/agent/graph.py`, focused `tests/test_agent_graph.py`, minimal
exports, `pyproject.toml`, and `uv.lock`. Existing node/state modules may receive
only integration fixes required by composition. Before adding the dependency,
verify its official synchronous API, current Python 3.14 support, Pydantic
compatibility, and `uv` resolution. Select and record a narrow compatible range
without upgrading unrelated packages; do not add checkpointer/provider extras.

**Implementation scope:** Build a graph with exactly these logical nodes:
`analyze_goal`, `load_context`, `generate_plan`, `validate_plan`,
`request_approval`, `execute_tasks`, `verify_result`, and `summarize`. Use explicit
START/END edges and pure allowlisted route functions. The normal route is
analysis -> context -> generation -> validation -> approval -> execution ->
verification -> summary. Rejected plans route to summary without writes; change
requests return to generation only within the two-revision bound and must pass
validation again; invalid/failure paths terminate safely. Build/compile receives
provider, gateway, trusted context, approval adapter, and clocks as host-owned
dependencies or closures outside graph state.

**Interfaces and contracts:** Invoke with strict `AgentGraphInput` and return only
`AgentGraphOutput`. No recursion/step limit is caller controlled; configure a
fixed ceiling consistent with the finite edge/revision bounds and fail closed if
exceeded. Use the synchronous graph API because the application/domain stack is
synchronous. Ordinary execution uses deterministic fakes and never reads a real
provider key.

**Explicitly not included:** No checkpointer, persistence, database migration,
`thread_id`/`run_id`, durable interrupt/resume, service-restart recovery,
idempotency/replay, approval Router, public Agent HTTP endpoint, SSE/WebSocket,
background worker, RAG, tracing vendor, MCP, multi-agent, AsyncSession, or Stage
10 code.

**Automated tests:** Cover graph structure and exact node names; happy no-action
and approved-action paths; reject path with zero writes; one/two edit cycles;
third-edit safe termination; validation failure; provider/context/tool/approval
failure; partial execution; exact edge order; revalidation before every approval;
fixed recursion ceiling; one terminal output; serializable state updates; runtime
identity isolation; provider/gateway/approval fake injection; no real network or
database; public output whitelist; and no Sessions, ORM objects, clients, keys,
tokens, prompts, raw payloads, diagnostics, or hidden reasoning in state/errors.

**Focused validation commands:** Run `tests/test_agent_graph.py`, every Stage 9
node/state test, all Stage 8 Agent regressions, and affected Task Service/Tool
tests. Then run the full ordinary and warnings suites, Ruff, format check, mypy,
`uv lock --check`, and `git diff --check`. Confirm default pytest still excludes
`integration` and `external_provider`. Do not start Docker or call a real model.

**Acceptance criteria:** A scripted offline provider and fake approval adapter
drive the complete graph through success, reject, edit, and failure branches;
every route terminates within exact bounds; writes require trusted approval and
Tool capability; output is strict and safe; no Stage 10 persistence behavior is
present.

**Learning points:** Graph composition from independently tested nodes;
conditional cycles with hard termination; runtime dependencies versus persisted
state.

**Stop boundary:** Task 9.7 completes Stage 9. Stop for owner confirmation. Do
not expose a public Agent API, add checkpoint/persistence/HITL recovery/SSE, begin
Stage 10, add RAG, MCP, multi-agent behavior, or resume deferred Stage 5 work.

### Stage 10 — Persistence, HITL, and streaming

Separate LangGraph recovery state from product audit records and make execution
safe across retries, process restarts, and authenticated HTTP requests. Stage 10
adds durable execution around the accepted synchronous Stage 9 graph; it does
not create a second authorization or domain-persistence stack.

The durable execution path is:

```text
Authenticated user
    -> stable thread_id and run_id
    -> owner-scoped business thread/run record
    -> LangGraph with official PostgreSQL checkpointer
    -> durable approval interrupt
    -> authenticated approve/reject/edit resume
    -> idempotent allowlisted Agent tool
    -> existing Domain service and Repository
    -> PostgreSQL
    -> safe run snapshot and SSE progress
```

Stage 10 keeps three data concerns separate:

1. Existing domain tables (`users`, `projects`, and `tasks`) remain the source of
   truth for user-owned learning data and are changed only through Domain
   Services.
2. Product-owned Agent business records store owner-scoped thread/run identity,
   approval decisions, safe execution summaries, outcomes, stable error codes,
   and bounded metrics required by the product and its audit API.
3. Official LangGraph checkpoint tables store graph recovery state and are
   created, migrated, and accessed only through the official persistence
   implementation. They are not queried through product repositories and are
   never presented as the product audit API.

Business records and public events never store or expose hidden reasoning,
complete prompts/model responses, raw sensitive tool payloads, API keys, access
tokens, `Authorization` headers, Sessions, connections, ORM objects, complete
database URLs, or raw database/provider diagnostics. Agent state continues to
contain only validated JSON-serializable values. The existing application and
Domain Services continue to use synchronous SQLAlchemy `Session`; adopting a
LangGraph checkpointer is not permission to introduce `AsyncSession`.

The Stage 10 result milestones are:

1. Task 10.1 independently fixes and migrates the product-owned audit data
   contract before a separate persistence library is introduced.
2. Tasks 10.2–10.3 integrate the official checkpointer and bind stable,
   owner-scoped thread/run identities to graph configuration.
3. Task 10.4 introduces durable authenticated interrupt/resume without yet
   expanding the write-tool surface.
4. Tasks 10.5–10.6 make replayed and high-impact writes idempotent and explicitly
   approved.
5. Tasks 10.7–10.8 add safe SSE delivery and prove restart recovery, ownership,
   migrations, and audit behavior against real PostgreSQL.

### Task 10.1 — Thread/run/approval business records and reversible migration

**Estimated time:** 1–2 focused hours. **Migration/real PostgreSQL:** one new
application Alembic revision and guarded `postgres-test` verification are
required. **New dependency:** none.

**Goal:** Establish the minimal product-owned records needed to identify and
audit Agent threads, individual runs, and human approval decisions without
using LangGraph checkpoint rows as business data.

**Prerequisites:** Stage 9 is accepted and committed; the unique Alembic head is
`6e2f9a4c1b73`; the existing UUID, UTC timestamp, owner-scoped foreign-key,
named-constraint, synchronous Session, migration-safety, and public-schema
conventions remain authoritative.

**Files:** New `app/models/agent_run.py`, `app/schemas/agent_run.py`, and one new
revision under `alembic/versions/`; update `app/models/__init__.py` so Alembic
metadata discovers the new models; add focused `tests/test_agent_run_models.py`
and `tests/test_agent_run_schemas.py`, plus one narrow real-PostgreSQL migration
test under `tests/integration/`. Use a different equally clear filename only if
the implementation task first finds an established repository naming conflict.

**Implementation scope:** Add product tables `agent_threads`, `agent_runs`, and
`agent_approvals`. Every table uses PostgreSQL UUID primary keys generated by the
database, timezone-aware UTC `created_at`/`updated_at`, explicit named primary,
foreign-key, check, unique, and index contracts, and bounded strings. A thread is
owned by one `users.id` and stores only a bounded safe goal summary and lifecycle
status. A run belongs to one thread and the same owner, stores its graph status,
current safe node name, bounded terminal summary/error code, prompt version,
and bounded aggregate metrics; it does not store graph state or raw model/tool
payloads. An approval belongs to one run and stores revision, proposal
fingerprint, pending/approved/rejected/changes-requested decision, bounded safe
feedback, decision timestamp, and the deciding authenticated user. A named
unique constraint on `(run_id, revision)` prevents two business approval rows
for the same proposal revision. Define strict internal/public Pydantic contracts
that whitelist identifiers, safe statuses, timestamps, summaries, and metrics.
The new migration has `down_revision = "6e2f9a4c1b73"`, creates only these
product tables in dependency order, and drops only them in reverse order.

**Explicitly not included:** No official Checkpointer dependency or tables,
repository/service, graph compile change, HTTP Router, interrupt/resume, Tool
execution record, idempotency key, high-impact Tool, SSE, background worker,
RAG, or Stage 11 behavior. Do not modify a historical migration.

**Automated tests:** Prove exact columns/types/nullability/defaults, named
constraints/indexes, owner and parent foreign keys, allowed statuses, bounded
safe fields, aware timestamp/public whitelist behavior, extra-field rejection,
and absence of prompt/response/action arguments/credentials. The PostgreSQL
migration test must upgrade from `6e2f9a4c1b73` to the new revision, inspect all
three tables and names, downgrade to `6e2f9a4c1b73` and confirm only these tables
are removed while Stage 7 domain tables remain, then re-upgrade and restore the
current head.

**Focused validation commands:** Run the model/schema tests, the focused marked
migration test on guarded `postgres-test`, `alembic current`, `alembic heads`,
`alembic check`, affected metadata/migration regressions, Ruff, format, mypy,
lock, and diff checks. Start and stop only `postgres-test`; never touch
`postgres-dev` or volumes.

**Acceptance criteria:** Product-owned thread/run/approval contracts are
discoverable in SQLAlchemy metadata and reproducible by one reversible linear
migration; ownership and approval-revision integrity have database defenses;
no Checkpoint or sensitive execution content is represented as business audit
data.

**Learning points:** Product audit data versus recovery state; composite
ownership/integrity constraints; reversible schema evolution at a stage boundary.

**Stop boundary:** Stop after models, strict schemas, migration, and their tests.
Do not install a Checkpointer, add repositories/services/routes, compile the
graph with persistence, or start Task 10.2.

### Task 10.2 — Official PostgreSQL LangGraph checkpointer integration

**Estimated time:** 1–2 focused hours. **Migration:** no application Alembic
revision. **Real PostgreSQL:** required only in a marked focused integration
test. **New dependency:** the minimal official PostgreSQL Checkpointer package,
added only in this task after compatibility verification.

**Goal:** Provide one replaceable synchronous checkpoint factory/lifecycle that
can compile the Stage 9 graph for durable recovery without leaking persistence
objects into graph state or product repositories.

**Prerequisites:** Task 10.1 is accepted and committed. Before editing, verify
the official package name, supported synchronous API, current LangGraph range,
Python 3.14 compatibility, PostgreSQL driver expectations, setup/migration
method, and `uv` resolution from primary documentation. Stop on an unresolved
compatibility or lifecycle conflict rather than guessing.

**Files:** A narrowly named module such as `app/agent/checkpointing.py`, minimal
configuration additions in `app/core/config.py` and `.env.example` only when an
independent checkpoint database setting is required, `pyproject.toml`,
`uv.lock`, focused `tests/test_agent_checkpointing.py`, and a marked
`tests/integration/test_agent_checkpointing.py`. Change `app/agent/graph.py`
only enough to accept a host-owned checkpointer at compile time while preserving
the existing no-checkpointer offline path and tests.

**Implementation scope:** Wrap the official synchronous PostgreSQL Checkpointer
behind a small application-owned factory/context boundary. Configuration must
come from a secret-aware environment setting with no unsafe production default.
Create/setup official checkpoint tables solely through the official library,
not SQLAlchemy models or Alembic. Pass the checkpointer to LangGraph compilation
and stable `thread_id` through LangGraph configuration, never through
model-visible state. Close every checkpointer connection/pool deterministically.
Keep offline graph construction possible with no database. Clearly document in
code that product repositories must not query official checkpoint tables.

**Explicitly not included:** No `AsyncSession`, async application rewrite,
business audit CRUD, new application migration, public API, authenticated
thread/run service, interrupt/resume, idempotency, SSE, provider network call,
or hand-written checkpoint schema.

**Automated tests:** With a fake factory, prove lazy construction, exact stable
configuration, graph state exclusion, offline compatibility, safe missing/
invalid configuration errors, and closure on success/failure. A marked real
PostgreSQL test must use the guarded test target, run official setup, write and
read one synthetic checkpoint through the public Checkpointer API, create no
application audit record implicitly, and avoid inspecting or asserting private
table layouts beyond official readiness behavior.

**Focused validation commands:** Run checkpointing and graph unit tests; then on
`postgres-test` run only the marked checkpoint integration test using synthetic
state and no real provider. Run Ruff, format, mypy, lock, and diff checks. Confirm
no application Alembic revision or unrelated dependency upgrade appeared.

**Acceptance criteria:** A synchronous Stage 9 graph can be compiled with an
official PostgreSQL Checkpointer and recover synthetic serializable state by
stable thread configuration; the existing offline graph remains deterministic;
connections close and no Checkpoint row becomes product audit output.

**Learning points:** Library-owned versus application-owned schema lifecycle;
dependency injection for persistence; synchronous Checkpointer lifecycle versus
SQLAlchemy Session lifecycle.

**Stop boundary:** Stop after the Checkpointer adapter, compile seam, and focused
tests. Do not build owner-scoped runs, approval endpoints, interrupts, replay, or
SSE.

### Task 10.3 — Stable `thread_id`/`run_id` creation and ownership lookup

**Estimated time:** 1–2 focused hours. **Migration/new dependency:** none.
**Real PostgreSQL:** optional focused integration only if unit fakes cannot prove
a discovered constraint behavior.

**Goal:** Create and retrieve product Agent threads/runs with stable UUIDs and
bind their owner-checked identity to LangGraph configuration without letting the
model or another user select ownership.

**Prerequisites:** Tasks 10.1–10.2 are accepted; the business models and
Checkpointer lifecycle are fixed; current-user authentication and safe 404
conventions are unchanged.

**Files:** New `app/repositories/agent_runs.py` and
`app/services/agent_runs.py`, focused `tests/test_agent_run_repository.py` and
`tests/test_agent_run_service.py`, and minimal additions to
`app/schemas/agent_run.py`. Use the existing synchronous `Session` and current
clock/factory injection styles.

**Implementation scope:** Repository operations create and owner-scope threads,
runs, and approvals, call `add`/`flush` or queries, and never commit or translate
HTTP. Service functions create a new thread plus initial run in one transaction,
create a later run for an owned thread, and retrieve an owned thread/run using
both resource and authenticated `user_id`; foreign-owned and absent identifiers
share a safe domain 404. The service, not the client/model, supplies `user_id`,
generates or accepts host-generated stable UUIDs, commits success, rolls back
failure, and returns strict public schemas. Provide one pure mapping from the
owned thread ID to LangGraph `configurable.thread_id`; keep `run_id` as product
execution identity and never confuse it with a Checkpoint ID. Repeated creation
requests are not yet idempotent unless a caller supplies a separately validated
operation key introduced in Task 10.5.

**Explicitly not included:** No Router, graph execution, approval decision,
interrupt/resume, Tool write, idempotency table, SSE, checkpoint-table query,
provider call, or migration.

**Automated tests:** Cover new thread/initial run, later run for an owned thread,
exact owner predicates, cross-user safe 404, generated/stable UUID round trips,
thread/run distinction, correct LangGraph configuration, commit/refresh on
success, rollback on every write failure, no commit for reads, repository
`flush` without commit, strict public fields, and no caller/model `user_id`,
Session, token, or checkpoint internals in schemas or errors.

**Focused validation commands:** Run run repository/service/schema tests,
current-user ownership regressions, Stage 9 state/graph tests, then Ruff, format,
mypy, lock, and diff checks. No Docker is required unless a concrete database
constraint defect needs focused proof.

**Acceptance criteria:** Trusted application code can create and owner-safely
look up stable thread/run identities and derive the exact Checkpointer thread
configuration; Service/Repository transaction ownership matches the rest of the
application; no graph has been started or resumed.

**Learning points:** Product run identity versus graph checkpoint identity;
owner-scoped repository predicates; transactionally creating an aggregate root
and its first run.

**Stop boundary:** Tasks 10.2–10.3 form the second Stage 10 result milestone.
Stop before public run routes, graph invocation, interrupt/resume, idempotent
writes, or SSE.

### Task 10.4 — Approve, reject, and edit interrupt/resume workflow

**Estimated time:** 1–2 focused hours. **Migration/new dependency:** none.
**Real PostgreSQL:** a marked focused integration test is required because
durable pause/resume cannot be proven by only an in-memory fake.

**Goal:** Replace the Stage 9 in-process approval decision for durable execution
with a LangGraph interrupt that can be resumed exactly once by the authenticated
owner through strict approve, reject, or request-changes input.

**Prerequisites:** Tasks 10.1–10.3 are accepted; official Checkpoint recovery,
owned run lookup, and the Stage 9 fingerprint/revision limits are stable.

**Files:** Minimal durable extensions to `app/agent/graph.py` and
`app/agent/nodes/approval.py`; new orchestration service such as
`app/services/agent_workflow.py`; strict request/response additions in
`app/schemas/agent_run.py`; new `app/api/v1/endpoints/agent_runs.py` and minimal
router inclusion; focused `tests/test_agent_workflow_service.py` and
`tests/test_agent_run_api.py`; marked focused integration coverage under
`tests/integration/`.

**Implementation scope:** Add authenticated endpoints to start one bounded Agent
run, retrieve its safe owner-scoped snapshot, and submit one approval decision
for a pending run. The start path creates product identities, invokes the graph
with the owned `thread_id`, and records a pending approval when LangGraph
interrupts. The resume path validates owner, run/thread relation, pending status,
current revision, and proposal fingerprint before calling `Command(resume=...)`
or the exact official synchronous equivalent. Approve may continue to execution;
reject terminates with zero new writes; request-changes carries bounded untrusted
feedback back through generation, deterministic validation, and a fresh
interrupt. At most two edits remain allowed. Persist the business decision and
run status transactionally around the orchestrator's documented failure points;
return safe conflict/not-found errors for already-decided, terminal, stale, or
foreign-owned attempts.

**Explicitly not included:** No SSE, polling worker, background queue,
idempotent Tool replay, batch/delete Tool, Stage 9 in-process adapter as durable
storage, Checkpoint internals in responses, or Stage 11 behavior.

**Automated tests:** Cover start-to-interrupt, exact safe approval payload,
approve/reject/edit, revalidation and fresh approval after edit, second-edit
limit, stale fingerprint/revision, duplicate resume, terminal run, missing and
foreign-owned safe 404, owner identity only from authentication, Service
commit/rollback, process-local object loss followed by Checkpointer resume in a
focused PostgreSQL test, and redaction of prompt/action arguments/tokens/hidden
reasoning/checkpoint data.

**Focused validation commands:** Run workflow service, Agent run API, approval,
graph, authentication, route-whitelist, and OpenAPI tests; run the marked durable
interrupt/resume test on guarded `postgres-test`; then Ruff, format, mypy, lock,
and diff. Do not call a real provider—use the deterministic scripted provider.

**Acceptance criteria:** An authenticated owner can start a run, observe a safe
pending approval, and durably approve, reject, or request bounded changes after
process-local dependencies are rebuilt; duplicate/stale/cross-user resumes fail
safely and no model output can approve itself.

**Learning points:** LangGraph interrupt versus in-process callback; optimistic
approval identity with proposal fingerprints; coordinating product transactions
with an external persistence boundary.

**Stop boundary:** Task 10.4 is its own review milestone. Do not add idempotency,
new high-impact Tools, SSE, RAG, or final Stage 10 recovery coverage.

### Task 10.5 — Tool idempotency and duplicate-write prevention

**Estimated time:** 1–2 focused hours. **Migration/real PostgreSQL:** one new
reversible application migration and a focused concurrency/replay integration
test are required. **New dependency:** none.

**Goal:** Ensure a resumed, retried, or duplicated approved action cannot repeat
a domain write, while accurately distinguishing completed, failed, in-progress,
and unknown outcomes.

**Prerequisites:** Task 10.4 is accepted; action keys, proposal fingerprints,
run identity, and Tool execution order are stable.

**Files:** New `app/models/agent_tool_execution.py` or an equally narrow model,
one Alembic revision whose `down_revision` is Task 10.1's application revision,
model export update, `app/repositories/agent_tool_executions.py`,
`app/services/agent_tool_executions.py`, minimal execution-node/workflow wiring,
strict safe schema additions, focused unit tests, and one marked integration
test. Do not alter official Checkpoint tables.

**Implementation scope:** Persist one product execution-intent row before each
approved write using a deterministic idempotency identity derived from owned
`run_id`, validated proposal fingerprint, revision, and action key—not raw model
arguments. A named database unique constraint is the concurrency and replay
final defense. The service claims an action, executes the existing allowlisted
Tool once, and records only safe status, tool name, public result identity/
summary, error code, attempt timing, and timestamps. A repeated completed action
returns the prior safe result without calling the Tool; an in-progress or
unknown-outcome action fails closed for explicit reconciliation; an accepted
retry policy may retry only outcomes proven to have produced no domain write.
Repository operations only query/add/update/flush; the coordinating Service owns
commit/rollback. Handle the named unique-constraint race, not every
`IntegrityError`, as an idempotent duplicate claim.

**Explicitly not included:** No compensation, distributed lock, arbitrary
caller idempotency key, hidden payload persistence, automatic retry of unknown
outcomes, batch/delete Tool, SSE, or Checkpoint-table query.

**Automated tests:** Cover first execution, completed replay with zero Tool
calls, two-session deterministic claim race, named unique constraint, failed and
unknown outcomes, safe retry eligibility, crash points before/after domain
commit, owner/run/action binding, repository transaction discipline, public
result reconstruction, and no sensitive arguments/identity/diagnostics in
records or errors. Migration tests cover upgrade, downgrade to Task 10.1 head,
re-upgrade, and metadata drift while retaining all Task 10.1 tables.

**Focused validation commands:** Run idempotency service/node/model tests and
the guarded PostgreSQL migration/concurrency/replay tests; run Stage 9 execution
and Task Service regressions; then `alembic current`, `heads`, `check`, Ruff,
format, mypy, lock, and diff. Stop only `postgres-test` afterward.

**Acceptance criteria:** Replaying the same accepted action cannot create a
second domain write; concurrent claims have one database winner; ambiguous
outcomes are visible and fail closed; the audit record contains only safe
bounded data.

**Learning points:** At-most-once intent versus exactly-once claims; database
uniqueness as the replay final defense; crash windows across separate use-case
transactions.

**Stop boundary:** Stop after idempotency storage and integration with the
existing two Task write tools. Do not add high-impact Tools, bulk behavior, SSE,
or final recovery tests.

### Task 10.6 — Mandatory approval for batch creation, deletion, and high impact

**Estimated time:** 1–2 focused hours. **Migration:** one corrective revision
widens only `ck_agent_tool_executions_tool_name` for the two new capabilities.
**New dependency:** none.
**Real PostgreSQL:** optional only when a focused transaction/ownership defect is
found; ordinary acceptance uses service/tool fakes.

**Goal:** Define an explicit deterministic high-impact capability policy and
ensure batch task creation and task deletion cannot execute without a matching
persisted approval and idempotent action claim.

**Prerequisites:** Task 10.5 is accepted; durable approval and idempotent Tool
execution are available; existing Task create/delete owner rules are stable.

**Files:** Extend `app/agent/tools.py`, the Stage 9 proposal/validation contracts,
execution policy modules or add one narrow `app/agent/policy.py`, and
`app/services/agent_domain.py`; add focused
`tests/test_agent_high_impact_policy.py` and update relevant Tool, planning,
approval, execution, and graph tests. Add a dedicated Domain Service operation
for bounded batch creation only if no existing service can own one atomic batch
transaction without weakening current layering. Add one reversible migration that
replaces only the existing Tool-name check constraint; do not edit Task 10.5's
accepted migration.

**Implementation scope:** Add only the explicitly named `batch_create_tasks` and
`delete_task` capabilities. Batch input contains one to at most ten strict
`TaskCreate` items and uses an owned Project; deletion accepts only `task_id`.
Classify these two tools as high impact in a code-owned frozen allowlist. The
model may propose them but cannot set `approved`, `user_id`, idempotency status,
or transaction options. Deterministic validation marks their approval scope;
execution requires a matching persisted approved proposal fingerprint/revision
and a successfully claimed idempotency record before invoking the Domain
Service. Batch creation owns one explicit Service transaction so it is all-or-
nothing; delete reuses the existing owner-scoped safe 404 behavior. Rejection,
stale approval, write-disabled runtime, or missing idempotency claim causes zero
domain calls.

**Explicitly not included:** No project deletion, arbitrary bulk Tool, more than
ten writes, model-selected policy, self-approval, compensation workflow,
background execution, SSE, or Stage 11 behavior.

**Automated tests:** Cover the exact expanded allowlist/schema, batch bounds and
atomic Service behavior, delete ownership, cross-user safe 404, policy
classification, mandatory matching approval, stale/wrong-run approval, no Tool
call before approval/idempotency, duplicate execution returning the accepted
prior result, write-disabled context, rollback on one invalid batch item, public
output bounds, and redaction of item payloads, identity, tokens, and diagnostics.

**Focused validation commands:** Run high-impact policy, Agent read/write Tool,
planning/approval/execution/graph, Task Service/delete, and idempotency tests,
then the full ordinary suite, Ruff, format, mypy, lock, and diff. Do not start
Docker unless a concrete PostgreSQL-only defect requires proof.

**Acceptance criteria:** High-impact capability is a deterministic application
policy, not model data; batch creation and deletion require current durable
approval and idempotency; ownership and transaction rules continue through
Domain Services.

**Learning points:** Risk-based capability gating; approval binding versus a
boolean flag; atomic bounded batch writes through one Service transaction.

**Stop boundary:** Tasks 10.5–10.6 form the write-safety milestone. Do not add
SSE, additional Tool families, background workers, RAG, or Stage 11 behavior.

### Task 10.7 — SSE node/tool/progress streaming without hidden reasoning

**Estimated time:** 1–2 focused hours. **Migration/new dependency:** none unless
the implementation proves the current framework cannot emit standards-compliant
SSE; do not add a package speculatively. **Real PostgreSQL:** use fakes for
ordinary tests and the accepted run store for focused API behavior.

**Goal:** Expose an authenticated, owner-scoped Server-Sent Events stream of
safe persisted Agent progress without turning internal graph state or reasoning
into a public event log.

**Prerequisites:** Tasks 10.1–10.6 are accepted; public run statuses, safe audit
summaries, durable approval, and execution outcomes are stable.

**Files:** New strict `app/schemas/agent_events.py` and a narrow streaming
service such as `app/services/agent_events.py`; extend the Agent run endpoint
and router; update `app/agent/events.py` only to map accepted internal events to
the public contract; add focused `tests/test_agent_sse.py` plus affected OpenAPI,
route, auth, and redaction regressions.

**Implementation scope:** Add an authenticated owner-scoped SSE endpoint for one
run. Define versioned event types for run/node status, Tool start/result summary,
approval required, safe metrics, heartbeat, terminal result, and safe error.
Every event has a stable monotonically ordered event ID within the run, run ID,
event type, aware UTC timestamp, and strict bounded public payload. Specify and
test deterministic ordering, one terminal event, heartbeat with no business
payload, disconnect cancellation/cleanup, and `Last-Event-ID` resume from the
product event/audit boundary without reading Checkpoint internals. Completed
runs can replay only bounded retained public events or return the final safe run
snapshot according to one documented contract. Streaming must not hold a
SQLAlchemy Session open while awaiting the client; services perform short reads.

**Explicitly not included:** No WebSocket, hidden chain-of-thought, token-by-token
model text, complete prompts/responses, raw Tool arguments/results, polling
worker, message queue, Redis, unrestricted event retention, RAG, or public
Checkpoint inspection.

**Automated tests:** Cover media type and framing, exact event schemas/order/IDs,
node and Tool summaries, approval and terminal events, heartbeat, reconnect from
valid/stale/foreign run event IDs, completed stream, disconnect cleanup, owner
safe 404, authentication 401, no long-lived Session, bounded output, and
redaction of credentials, identity internals, prompts, arguments, diagnostics,
database URLs, and hidden reasoning. Keep tests deterministic without sleeps.

**Focused validation commands:** Run SSE service/API, events, run API,
authentication, graph, idempotency, OpenAPI, and strict route tests, followed by
the full ordinary/warnings suite, Ruff, format, mypy, lock, and diff. No real
provider is allowed.

**Acceptance criteria:** A client can observe and reconnect to one owned run's
ordered safe progress through standards-compliant SSE; disconnects close
resources; internal state and sensitive data never cross the public event
whitelist.

**Learning points:** SSE framing and reconnection; durable public events versus
ephemeral graph callbacks; resource lifetime for streaming responses.

**Stop boundary:** Stop after safe SSE transport and focused tests. Do not run
the complete restart/migration matrix, add WebSockets, RAG, tracing vendors, or
Stage 11 code.

### Task 10.8 — Restart recovery, audit-summary, and PostgreSQL tests

**Estimated time:** 1–2 focused hours. **Migration:** no new revision; validate
all Stage 10 revisions. **Real PostgreSQL/Docker:** required. **New dependency:**
none.

**Goal:** Prove the complete Stage 10 durable workflow against the dedicated
PostgreSQL service, document its safe public behavior, and close the stage with
repeatable recovery, ownership, idempotency, migration, and security evidence.

**Prerequisites:** Tasks 10.1–10.7 are accepted and separately checkpointed;
the dedicated test-database safety gate, port override, synthetic Provider, and
precise cleanup conventions are green.

**Files:** New or expanded focused tests such as
`tests/integration/test_agent_recovery.py`,
`tests/integration/test_agent_idempotency.py`, and
`tests/integration/test_agent_audit.py`; update `README.md` with the implemented
Stage 10 run/approval/SSE contract and safe local workflow. Modify production
code only through a separate correction task if final testing proves a defect.

**Implementation scope:** With a scripted offline Provider and real
`postgres-test`, run the public authenticated start, inspect, approval/resume,
write, and SSE paths. Explicitly discard all process-local workflow/factory
objects between interrupt and resume, rebuild them, and recover through the
official Checkpointer plus product identities. Verify approve, reject, edit,
duplicate/stale resume, cross-user access, process restart, first/partial failure,
idempotent replay, deterministic two-session duplicate claim, high-impact
approval, safe audit summary, ordered SSE reconnect, and exact cleanup. Product
audit reads must use product repositories only and must not expose or depend on
official Checkpoint table structure.

**Final Stage 10 acceptance matrix:**

| Area | Required proof |
| --- | --- |
| Identity | Stable UUID thread/run IDs, authenticated owner derivation, and cross-user safe 404 |
| Recovery | Interrupt, process-local object loss, rebuilt runtime, and successful Checkpointer resume |
| Decisions | Approve, reject, edit/revalidate/reapprove, stale and duplicate decision rejection |
| Writes | Named database idempotency constraint, one winner under concurrency, safe unknown outcome |
| High impact | Batch/delete require matching durable approval and cannot bypass capability policy |
| Audit | Product run/approval/execution summaries are bounded and separate from Checkpoint rows |
| Streaming | Ordered safe SSE, heartbeat, terminal event, reconnect, and disconnect cleanup |
| Security | No hidden reasoning, credentials, tokens, full prompts/payloads, URLs, or raw diagnostics |
| Database | Empty upgrade, downgrade to `6e2f9a4c1b73`, re-upgrade, exact constraints, `current`, `heads`, and no drift |
| Regression | Stage 8/9 offline behavior and all existing auth/project/task contracts remain green |

**Explicitly not included:** No real external Provider call, Stage 11 RAG/
pgvector/evaluation/tracing implementation, MCP, multi-agent orchestration,
Redis, queue, Kubernetes, Stage 5 refresh-token work, or destructive shared-data
cleanup.

**Automated and real verification:** Recreate only disposable `postgres-test`,
validate driver/host/database/user/configured port, upgrade an empty database to
head, run all Stage 10 integration tests, downgrade to Stage 9 head
`6e2f9a4c1b73`, prove all Stage 10 product tables are removed while domain tables
remain, re-upgrade, run `alembic current`, `heads`, and `check`, then rerun the
focused recovery suite. Precisely delete only test-owned thread/run/domain data
in dependency order. Run all ordinary and warnings tests, integration tests,
Ruff, format, mypy, lock, and diff checks. Finally stop only `postgres-test`;
never operate on `postgres-dev` or delete volumes.

**Acceptance criteria:** The matrix passes with actual evidence; an interrupted
workflow survives runtime reconstruction, cannot cross ownership or duplicate a
write, exposes only safe audit/SSE contracts, and leaves application metadata,
migrations, and PostgreSQL at the same unique head.

**Learning points:** Recovery testing versus same-process continuation;
end-to-end consistency across Checkpoint, audit, and domain commits; security
review of persisted and streamed Agent data.

**Stop boundary:** Task 10.8 completes Stage 10. Stop for owner confirmation. Do
not begin Stage 11, call a real model, add RAG/pgvector/tracing/evaluations, expose
MCP, or introduce multi-agent behavior.

### Stage 11 — Focused RAG, evaluation, security, and tracing

RAG is limited to user-uploaded syllabi, exam requirements, and study material
used to produce source-grounded learning plans. Uploaded and retrieved content is
always untrusted data: it cannot alter authenticated identity, ownership checks,
approval requirements, system policy, or the code-owned Tool allowlist. Ordinary
tests use deterministic parser, embedding, retrieval, and model fakes and never
call an external Provider.

### Task 11.1 — Owner-scoped document upload, parsing, and file safety

**Estimated time:** 1–2 focused hours. **Migration:** one reversible document
revision based on Stage 10 head `c4d8a1f6e205`. **Real PostgreSQL:** required.

**Goal and prerequisites:** After Stage 10 acceptance, let an authenticated user
upload a bounded syllabus, exam requirement, or study document and persist one
owner-scoped parsed record without exposing extracted text.

**Files:** New `app/models/knowledge_document.py`,
`app/schemas/knowledge_document.py`, `app/repositories/knowledge_documents.py`,
`app/services/knowledge_documents.py`,
`app/api/v1/endpoints/knowledge_documents.py`, one generated Alembic revision,
necessary router/model exports, `tests/test_knowledge_document_schemas.py`,
`tests/test_knowledge_document_service.py`, `tests/test_knowledge_document_api.py`,
and `tests/integration/test_knowledge_documents.py`. Add a maintained PDF parser
to `pyproject.toml`/`uv.lock` only after Python 3.14 compatibility is confirmed.

**Implementation scope:** Add `POST /api/v1/knowledge/documents` using
`multipart/form-data`. Allow `.txt`, `.md`, and unencrypted `.pdf` with matching
allowlisted media type and PDF signature. Bound raw input to 5 MiB, PDF input to
100 pages, display name to 255 characters, and normalized extracted Unicode text
to 200,000 characters. Reject empty, encrypted, malformed, unsupported,
oversized, or undecodable input with fixed safe errors. Persist UUID, trusted
`user_id`, display name, media type, byte count, SHA-256 fingerprint, page count,
private extracted text, parse status, and UTC timestamps. Use named checks,
owner foreign key, `(id, user_id)` uniqueness, and owner/status indexes. Public
metadata excludes raw bytes and text. Repository flushes; Service commits or
rolls back; Router closes the upload.

**Not included:** Remote URLs, archives, Office/HTML, OCR, antivirus claims,
object storage, list/delete APIs, chunks, embeddings, pgvector, retrieval, Agent
context, or model calls.

**Tests and commands:** Cover every size/page/name/text boundary, MIME/signature
mismatch, malformed/encrypted/blank content, parser failure rollback, owner
derivation/isolation, public whitelist, file closure, safe errors, transaction
ownership, migration constraints, and precise cleanup. Run focused tests and
ordinary quality gates; on guarded `postgres-test`, upgrade from
`c4d8a1f6e205`, inspect the table, downgrade and prove only it is removed,
re-upgrade, then run `alembic current`, `heads`, `check`, and integration tests.

**Acceptance criteria:** Supported files produce safe metadata and one private
owned row; rejected content leaves no row or sensitive echo; cross-user access is
safe 404; migration round-trip and quality gates pass.

**Learning points:** Streaming upload bounds; hostile parser input; private model
versus public schema. **Stop boundary:** Stop before chunks or embeddings.

### Task 11.2 — Deterministic chunking, embedding port, and pgvector storage

**Acceptance status (2026-09-06):** Accepted on the dedicated PostgreSQL 17
`postgres-test` service with pgvector 0.8.6. Migration upgrade/check and the
`d7a1e4c9b320` downgrade/re-upgrade round trip passed; PostgreSQL catalog checks
proved `vector(1536)`, generated `tsvector`, GIN, HNSW cosine, and owner-scoped
constraints, and the complete integration suite passed.

**Estimated time:** 1–2 focused hours. **Migration:** exactly one reversible
pgvector/chunk revision based on the Task 11.1 head. **Real PostgreSQL:** required;
external Provider access is not.

**Goal and prerequisites:** With Task 11.1 accepted, turn one owned parsed
document into deterministic chunks and fixed-size embeddings stored for lexical
and vector search. Both Compose PostgreSQL services must use the same pinned
pgvector-capable PostgreSQL 17 image before the migration runs.

**Files:** New `app/agent/embeddings.py`,
`app/models/knowledge_document_chunk.py`,
`app/repositories/knowledge_document_chunks.py`,
`app/services/knowledge_document_indexing.py`, one generated migration, focused
`tests/test_document_chunking.py`, `tests/test_embedding_provider.py`,
`tests/test_document_indexing_service.py`, and
`tests/integration/test_document_embeddings.py`. Minimally update settings,
document endpoint/schema, model exports, `alembic/env.py`, `compose.yaml`,
`.env.example`, README, `pyproject.toml`, and `uv.lock`; add only the compatible
`pgvector` package and image support.

**Implementation scope:** Define a synchronous replaceable `EmbeddingProvider`
Protocol, deterministic fake, and adapter over the existing SDK. Fix the MVP
contract at 1,536 finite dimensions and validate it at the adapter and Service.
Chunk by page and character boundary at no more than 2,000 characters, with
200-character overlap, stable ordinal/fingerprint, and at most 200 chunks per
document. An explicit owner-scoped indexing use case atomically replaces chunks;
upload does not call an embedding Provider automatically. The single migration
owns the `vector` extension and creates the chunk table with private text,
page/ordinal/fingerprint, `vector(1536)`, generated simple-configuration
`tsvector`, composite owner foreign key, named bounds/uniqueness, GIN lexical
index, and HNSW cosine index. Repository never commits.

**Not included:** Search, query embedding, hybrid ranking, reranker, prompt
context, background jobs, arbitrary dimensions, or real external embedding tests.

**Tests and commands:** Cover chunk boundaries/order/overlap, Unicode, maximum
count, stable fingerprints, vector length and non-finite rejection, bounded fake
calls, atomic replacement/rollback, ownership, safe adapter errors, metadata and
named indexes. Run focused tests; on guarded pgvector `postgres-test`, verify the
extension/table/vector/keys/indexes, downgrade to Task 11.1 retaining documents,
re-upgrade, and run `current`, `heads`, `check`, integration and all quality gates.

**Acceptance criteria:** Identical input yields identical chunks; invalid vectors
never persist; replacement is atomic; PostgreSQL proves lexical/vector storage;
ordinary tests remain offline.

**Learning points:** Provider ports; deterministic chunk identity; extension and
vector-index migrations. **Stop boundary:** Stop before retrieval.

### Task 11.3 — Owner-filtered vector retrieval and search Tool

**Acceptance status (2026-09-06):** Accepted on the dedicated PostgreSQL 17
`postgres-test` service. Real pgvector cosine queries proved owner and optional
document filters, bounded results, and stable chunk-ID tie breaking even when a
foreign owner's chunk was closer; the complete integration and ordinary quality
gates passed without a migration, dependency, HTTP endpoint, or external model
call.

**Estimated time:** 1–2 focused hours. **Migration/dependency:** none. **Real
PostgreSQL:** required for distance and ownership behavior.

**Goal and prerequisites:** After Task 11.2, retrieve relevant chunks for the
authenticated owner through one strict read-only Agent Tool.

**Files:** New `app/schemas/knowledge_retrieval.py` and
`app/services/knowledge_retrieval.py`; extend chunk Repository,
`app/agent/tools.py`, `app/services/agent_domain.py`, and exports; add
`tests/test_knowledge_retrieval.py`, `tests/test_agent_knowledge_tool.py`, and
`tests/integration/test_knowledge_retrieval.py`.

**Implementation scope:** Define a 1–2,000 character query, `top_k` 1–20, and at
most 20 optional document UUID filters. Embed once, then perform cosine search
with trusted `user_id` and owned-document predicates applied before ranking.
Return stable citation ID, document/chunk IDs, safe source name, page/ordinal,
distance, and at most 500 excerpt characters. Add only `search_knowledge`; model
arguments cannot contain `user_id`, Session, SQL, vectors, or operators. Tool
calls Gateway, Gateway closes one synchronous Session, Service/Repository own
retrieval, and reads do not commit.

**Not included:** Lexical/hybrid search, reranking, prompt/state changes, public
search endpoint, writes, migration, or real external Provider.

**Tests and commands:** Cover all query/filter/top-k bounds, exact Tool schema,
identity/vector/SQL rejection, one embedding call, owner predicates, deterministic
ties, public bounds, zero results, cross-user isolation, Session closure, no
commit, and safe errors. Run focused tests and guarded PostgreSQL tests with two
owners and deterministic vectors, followed by integration and ordinary quality
gates.

**Acceptance criteria:** At most 20 deterministic public citations are returned;
foreign chunks never appear even when closer; no network is required.

**Learning points:** Filter-before-rank authorization; cosine distance; safe
retrieval Tools. **Stop boundary:** Stop before hybrid fusion and prompt use.

### Task 11.4 — Hybrid retrieval, deterministic ranking, and citations

**Acceptance status (2026-09-06):** Accepted on the dedicated PostgreSQL 17
`postgres-test` service. Separate owner-filtered simple full-text and pgvector
candidate queries, fixed-constant RRF, stable chunk-ID ties, document filters,
and cross-owner isolation passed focused and complete test suites without a
migration, dependency, external model call, or prompt/state integration.

**Estimated time:** 1–2 focused hours. **Migration/dependency:** none. **Real
PostgreSQL:** required.

**Goal and prerequisites:** With Task 11.3 accepted, combine owner-scoped lexical
and vector candidates into stable cited evidence.

**Files:** Extend chunk Repository, retrieval Service/schema, and
`search_knowledge`; new `app/agent/retrieval.py`; add
`tests/test_hybrid_retrieval.py` and extend retrieval integration tests.

**Implementation scope:** Fetch separate bounded vector and PostgreSQL full-text
candidate lists, at most 40 each, then apply code-owned reciprocal-rank fusion
with chunk-ID tie breaking and at most 20 final results. The MVP reranker decision
is deterministic RRF: do not add a model/cross-encoder until evaluation proves a
need. Citation IDs derive from document/chunk identity and expose safe source,
optional page, excerpt, and rank evidence; citation does not claim factual truth.

**Not included:** Neural reranker, arbitrary weights, web/external corpus, prompt
assembly, plan changes, endpoint, migration, or extra model calls.

**Tests and commands:** Cover lexical-only/vector-only/overlap results, RRF math,
ties/caps, citation stability, missing page, both owner filters, and hostile text
remaining data. On guarded PostgreSQL, seed ranking disagreements for two owners
and prove fusion/isolation, then run retrieval/Tool, integration, Alembic check,
and ordinary quality gates.

**Acceptance criteria:** Fusion is deterministic, bounded, owner-safe, and every
excerpt maps to one stable source; no unmeasured reranker dependency is added.

**Learning points:** Lexical/vector complementarity; RRF; citation identity versus
verification. **Stop boundary:** Stop before Agent prompt/state integration.

### Task 11.5 — Grounded context and prompt-injection boundaries

**Acceptance status (2026-09-07):** Accepted with offline deterministic fakes.
The graph loads owner-scoped knowledge only through `search_knowledge`, stores at
most ten serializable public excerpts, renders a versioned 12,000-character
untrusted-data boundary, and validates every model citation before approval.
The final cited proposal is covered by the existing canonical approval
fingerprint; no migration, dependency, external call, or tracing was added.

**Estimated time:** 1–2 focused hours. **Migration/dependency:** none. **Real
PostgreSQL:** not required for ordinary acceptance.

**Goal and prerequisites:** After Task 11.4, place bounded cited evidence into the
existing graph without letting document instructions become policy or authority.

**Files:** New `app/agent/grounding.py`; extend `app/agent/state.py`, context node,
prompts, planning schemas, validation, and summary only as required; add
`tests/test_agent_grounding.py` and prompt/state/context/graph regressions.

**Implementation scope:** `load_context` calls `search_knowledge` through the
existing Tool executor with trusted identity and a bounded goal-derived query.
Store only serializable public evidence. Assemble at most 10 excerpts and 12,000
characters using explicit untrusted-data delimiters. Add bounded citation IDs to
plan output; deterministic validation rejects unknown citations and requires a
valid citation for grounded claims when evidence exists. Approval fingerprinting
covers the final cited proposal. Documents cannot alter identity, Tool allowlist,
approval, schema, or system instructions.

**Not included:** Complete documents in state/prompt, hidden reasoning,
instruction execution, authorization from content, web content, Tool expansion,
pre-approval writes, migration, or real model calls.

**Tests and commands:** Cover no-evidence fallback, order/size bounds, delimiter
handling, hostile identity/policy/self-approval/secret/Tool instructions, citation
validation, prompt version, checkpoint JSON round-trip, fingerprint changes, and
absence of complete content from errors/events/traces. Run focused fake-based
tests, Stage 8–10 regressions, and all ordinary quality gates.

**Acceptance criteria:** A strict cited plan can be built; fabricated citations
fail closed; adversarial content remains data and cannot change policy.

**Learning points:** Data/instruction separation; citation validation; bounded
serializable RAG state. **Stop boundary:** Stop before tracing/evaluation.

### Task 11.6 — Safe Agent tracing and bounded observability

**Acceptance status (2026-09-07):** Accepted with offline deterministic fakes.
The graph, node, Provider-attempt, read Tool, and durable Tool coordinator
boundaries emit strict `agent-trace.v1` allowlisted metadata through an injectable
synchronous sink. The default sink retains nothing, sink failures cannot affect
control flow or transactions, and traces remain absent from product audit, SSE,
checkpoints, database schema, and public APIs.

**Estimated time:** 1–2 focused hours. **Migration/dependency/PostgreSQL:** none.

**Goal and prerequisites:** After Task 11.5, trace nodes, Tools, safe errors,
tokens, and latency without recording content, credentials, or hidden reasoning.

**Files:** New `app/agent/tracing.py`; narrow instrumentation in graph/nodes,
Tool execution coordinator, and workflow composition; add
`tests/test_agent_tracing.py` plus metrics/graph/security regressions.

**Implementation scope:** Define injectable synchronous `TraceSink`, no-op
default, recording fake, and strict versioned event. Allow only run/thread IDs,
prompt version, node/Tool name, outcome, safe error code, attempts/counts, token
counts, and nonnegative latency. Bound keys/counts/strings and test a
non-disruptive sink-failure policy. Traces remain separate from product audit and
checkpoints; no vendor package is added.

**Not included:** Goal/document/prompt/model content, Tool arguments/results,
credentials, stack traces, database URLs, hidden reasoning, vendor export, new
table, public tracing API, or migration.

**Tests and commands:** Cover schema, correlations, start/finish/failure order,
retries, metrics, sink failure, unchanged control flow, and a sensitive-data
denylist across traces/logs/SSE/audit. Run tracing/metrics/graph/security focused
tests and all ordinary quality gates without Docker or network.

**Acceptance criteria:** Recording fakes receive useful operational evidence using
only allowlisted metadata; tracing cannot leak content or change results.

**Learning points:** Telemetry contracts; safe correlation; observability failure
isolation. **Stop boundary:** Stop before datasets or vendor tracing.

### Task 11.7 — Versioned offline evaluation dataset and runner

**Estimated time:** 1–2 focused hours. **Network/credentials/PostgreSQL:** none.

**Goal and prerequisites:** After Task 11.6, create a reproducible 30–50 case
offline benchmark using deterministic fakes and no domain writes.

**Files:** New `app/agent/evaluation.py`, `evals/stage11/dataset.v1.jsonl`,
`evals/stage11/README.md`, and `tests/test_agent_evaluation.py`.

**Implementation scope:** Add strict versioned case/expectation schemas and a
runner with injected model, embedding, retrieval, clock, and Tool fakes. Include
30–50 synthetic cases for extraction, Tool/argument validity, citations, plan
constraints, malicious documents, approval bypass, recovery, duplicates, safe
errors, latency, and token use. Default Tools are dry-run recorders. Output stable
per-case evidence and aggregate counts; malformed rows reveal only case ID.

**Not included:** Real Provider, production DB, hidden-reasoning grades,
subjective model judge, training, vendor eval platform, or threshold tuning after
seeing results.

**Tests and commands:** Prove version/count/unique IDs/category coverage,
deterministic order/output, fake-only execution, zero writes/network, malformed
bounds, safe reports, and injection coverage. Run the offline runner twice and
compare normalized output, then security and ordinary quality gates.

**Acceptance criteria:** A clean checkout runs 30–50 cases offline with identical
ordered evidence and no key, sensitive payload, network request, or write.

**Acceptance status:** Implemented with 39 synthetic `stage11-eval.v1` JSONL
cases spanning every declared category. The strict loader continues after an
invalid row using only a safe case ID; the injected offline runner emits stable,
bounded evidence and aggregate fact counts. Deterministic fakes and a dry-run
Tool recorder provide zero network, database, and real-write paths. Scoring,
thresholds, and baselines remain deferred to Task 11.8.

**Learning points:** Versioned eval contracts; reproducible dependency injection;
deterministic checks versus model judging. **Stop boundary:** Stop before scoring.

### Task 11.8 — Metrics and final Stage 11 verification

**Estimated time:** 1–2 focused hours. **Migration:** none; validate both Stage 11
revisions. **Real PostgreSQL:** required; external Provider is not.

**Goal and prerequisites:** With Tasks 11.1–11.7 accepted, calculate predeclared
quality/safety metrics and close Stage 11 with RAG, security, migration, and
observability evidence.

**Files:** New `app/agent/evaluation_metrics.py`,
`evals/stage11/baseline.v1.json`, and
`tests/integration/test_stage11_rag.py`; extend evaluation files/tests and README
only with implemented behavior, limitations, safe commands, and measured results.

**Implementation scope:** Calculate extraction exact match, Tool-name accuracy,
argument validity/accuracy, citation validity, plan-violation rate, unintended
writes, approval bypasses, recovery successes, duplicate writes, latency
percentiles, and token totals/averages. Define denominators, zero cases,
deterministic rounding, category counts, predeclared thresholds, and nonzero exit
on failure. Missing usage stays missing. Baseline stores only safe case IDs and
scores, never prompts, documents, model output, Tool arguments, credentials,
hidden reasoning, or database URLs.

**Not included:** Metric gaming, unversioned overwrite, chain-of-thought grading,
real Provider requirement, production data, dashboard/vendor export, Stage 12,
MCP, or multi-agent behavior.

**Tests and commands:** Unit-test every formula/threshold/redaction rule and
reproduce the offline baseline twice. On empty guarded pgvector `postgres-test`,
upgrade to Stage 11 head; verify document/chunk tables, owner keys, vector size,
indexes, and extension; run ownership/retrieval/grounding integration; downgrade
to `c4d8a1f6e205` proving Stage 11 tables are removed while Stage 10/domain tables
remain; re-upgrade; run `alembic current`, `heads`, `check`, full integration,
ordinary/warnings, eval, injection, approval/idempotency/recovery/tracing, Ruff,
format, mypy, lock, and diff checks. Stop only `postgres-test`.

**Acceptance criteria:** Predeclared metrics are reproducible and pass; hostile
documents cannot affect policy; owner filtering, citations, recovery/idempotency,
tracing redaction, migrations, and Stage 1–10 regressions pass without external
Provider or sensitive input.

**Acceptance status (2026-09-07):** Implemented with strict
`stage11-metrics.v1`, `stage11-gate.v1`, and `stage11-baseline.v1` contracts.
Predeclared thresholds, explicit zero-denominator and missing-usage behavior,
six-place half-up rounding, and nearest-rank latency percentiles are covered by
offline tests. The dedicated PostgreSQL 17 `postgres-test` catalog, complete
integration suite, and `c4d8a1f6e205` downgrade/re-upgrade round trip verify the
Stage 11 storage, owner isolation, retrieval, grounding, and migration boundary.

**Learning points:** Metric denominators; offline safety release gates; consistency
across RAG storage, policy, observability, and migrations.

**Stop boundary:** Task 11.8 completes Stage 11. Stop for owner confirmation; do
not begin Stage 12, call a real Provider, expose MCP, or add multi-agent behavior.

### Stage 12 — Deployment and job-search presentation

- **Task 12.1:** Application service in Docker Compose and one-command startup.
  **Acceptance status (2026-09-07):** Accepted with a pinned Python 3.14/uv
  image, frozen production dependency install, non-root runtime, fail-fast
  Alembic-before-Uvicorn startup, and `/health/ready` Compose healthcheck. The
  real one-command build/start and restart checks passed against only
  `postgres-dev`; `postgres-test` remained stopped and the development named
  volume was preserved. Compose contains no JWT fallback secret; authenticated
  use requires explicit shell or uncommitted `.env` injection.
- **Task 12.2:** GitHub Actions for tests, integration safety, Ruff, mypy, and lock.
  **Implementation status (2026-09-07):** Added an uncommitted two-job workflow
  for offline quality checks and a guarded, dedicated PostgreSQL 17 + pgvector
  integration service. Local static and ordinary quality checks pass. Docker was
  re-confirmed through its absolute executable path as Client/Server 29.7.2,
  Docker Desktop 4.87.0, `desktop-linux`, and Compose v5.4.0; direct sandbox
  failures are access limitations, not a missing installation. This repository
  is already linked to GitHub, but the Task 12.2 files have not been committed or
  pushed, so no GitHub Actions run includes this workflow yet.
- **Task 12.3:** Complete README, `.env.example`, API examples, and demo data.
  **Acceptance status (2026-09-07):** Accepted with distinct local uv and
  one-command Compose paths, a complete existing-variable configuration table,
  contract-checked PowerShell/curl examples for the public HTTP flows, and a
  1,657-byte synthetic Markdown syllabus accepted by the existing parser as
  `text/markdown`. Documentation explicitly separates the locally verified,
  unpushed Task 12.2 workflow from a future remote GitHub Actions run. Focused
  tests passed 26/26; the final ordinary suite passed 884 tests with 67 marked
  integration/external cases deselected, followed by Ruff, format, mypy, lock,
  and diff checks. No Docker, database, network, or real Provider was used.
- **Task 12.4:** System architecture and LangGraph state diagrams.
  **Acceptance status (2026-09-07):** Accepted with six GitHub-renderable
  Mermaid diagrams and text alternatives covering Compose/CI isolation, HTTP
  layering, the Agent Tool boundary, RAG and grounding, the exact eight-node
  approval graph, and separation of product audit, checkpoint, SSE, tracing,
  and offline evaluation. The data table matches the nine current product ORM
  tables and migration head `e3b7c2d9a410`; all 29 referenced paths exist.
  Focused graph/state/retrieval contracts passed 41/41, followed by Ruff,
  format, mypy, lock, and diff checks. No Docker, PostgreSQL, network, Provider,
  runtime code, schema, migration, dependency, or Task 12.3 artifact changed.
- **Task 12.5:** Reproducible demo/recording and test/evaluation results.
  **Acceptance status (2026-09-07):** Accepted with a no-Provider default
  runbook, an eight-minute recording checklist, and a dated bounded results
  snapshot. A real Compose build reached healthy app/PostgreSQL 17.11, and the
  public synthetic flow returned 200 for live/ready/OpenAPI/login/current-user
  and 201 for registration/Project/Task creation without printing credentials
  or tokens. Agent/RAG and offline-eval evidence passed 48/48 and 28/28; the
  focused set passed 46/46 and the final ordinary suite passed 884 tests with
  67 integration/external cases deselected. Ruff, format, mypy, lock, and diff
  checks passed. App and postgres-dev were stopped, the named volume remained,
  postgres-test stayed stopped, and no Provider, destructive migration, remote
  CI, commit, or push was used.
- **Task 12.6:** Failure cases, improvements, security statement, and cost notes.
  **Acceptance status (2026-09-07):** Accepted with an evidence-linked register
  of 18 failure modes, explicit proved/best-effort/non-guaranteed security
  classifications, a dependency-ordered P0–P3 improvement route, and formula-
  based local/Provider/CI/storage cost boundaries without unstable supplier
  prices. The disclosure calls out the missing bounded approval preview, lack of
  refresh/logout/rate limiting/production operations, citation truth limits,
  parser exposure, unpushed CI, and synthetic-eval extrapolation limits. All 40
  referenced evidence paths exist, secret patterns were absent, focused safety
  tests passed 77/77, and the final ordinary suite passed 884 tests with 67
  integration/external cases deselected, followed by Ruff, format, mypy, lock,
  and diff checks. No runtime behavior, Docker, database, Provider, network,
  migration, dependency, commit, or push was used.
- **Task 12.7:** Resume description and interview question/answer checklist.

### Checkpoint remediation before Task 12.7

**Status:** R1 through R6 completed local verification and were followed by the
owner's instruction to proceed between each remediation. The owner accepted R6
by authorizing the next gate, and the independent pre-commit review of the
combined Stage 11 through Task 12.6 candidate checkpoint completed on
2026-09-16 with no P0, P1, or P2 blocker. Checkpoint commit and push remain
pending explicit owner authorization. These six tasks are corrective gates for
that checkpoint, not new numbered Stage 12 features.
They do not renumber, implement, or change the scope of Task 12.7, and they do
not implement the separately unscheduled approval-preview P0.

- **R1 — CI runtime parity:** Add an explicitly test-only, job-scoped synthetic
  `STMS_ACCESS_TOKEN_SECRET` to the integration job; prove the no-secret failure,
  the configured 66-test integration pass, the static workflow contract, and
  that the offline quality job receives no unnecessary sensitive configuration.
  Plan: [checkpoint-remediation-r1-ci-runtime.md](tasks/checkpoint-remediation-r1-ci-runtime.md).
  **Local acceptance status (2026-09-12):** Completed. Without the signing value,
  the exact integration selection exited 1 with 47 passed and 19 authentication-
  configuration failures. With the workflow's test-only job-scoped value, the
  same selection passed 66/66. The focused contract set passed 40 tests, and the
  final ordinary suite passed 885 tests with 67 integration/external cases
  deselected. Ruff, formatting, mypy, lock, staged/unstaged diff, and bounded
  secret-pattern checks passed. The pre-existing staged candidate fingerprint
  remained unchanged. No commit, push, or remote GitHub Actions run occurred.
- **R2 — Compose network boundary:** Bind app, postgres-dev, and postgres-test to
  `127.0.0.1` by default while preserving port variables; require any remote
  exposure to use an explicit additional override and verify both parsed Compose
  configuration and runtime port bindings. Plan:
  [checkpoint-remediation-r2-compose-network.md](tasks/checkpoint-remediation-r2-compose-network.md).
  **Local acceptance status (2026-09-12):** Completed. Compose JSON parsing proves `host_ip` is
  `127.0.0.1` for all three services at default ports 8000/5432/5433 and overrides
  18000/15432/15433. Actual healthy postgres-test runs proved one IPv4 loopback
  binding at both 5433 and 15433; its default 5433 configuration was restored
  and the service stopped. The two parser tests executed (no skips): focused
  tests passed 5/5, and the full ordinary suite passed 887 with 67 deselected.
  Lock, Ruff, format, mypy, Compose config, and staged/unstaged diff checks passed.
  After explicit owner authorization, Compose recreated app and postgres-dev
  while retaining `fastapi-stms-postgres-dev-data`. Runtime inspection proved
  loopback-only bindings at 8000 and 5432, Uvicorn retained its container-internal
  `0.0.0.0:8000` listener, and live/ready both returned 200. The development
  migration revision and every table count matched the pre-recreation snapshot
  (including one user, one project, and one task). Both services were then
  stopped; postgres-test remained stopped and no volume was deleted. The initial
  `--build` attempt could not reach GHCR, so runtime verification used the
  existing application image; R2 changes no image content. No commit, push, or
  remote GitHub Actions run occurred. The owner then instructed work to proceed
  to R3.
- **R3 — Ingestion boundaries:** Add a pure ASGI streaming total-body limit before
  multipart parsing without rejecting a legal 5 MiB file, retain endpoint/service
  content checks, and reject U+0000 from every upload-derived PostgreSQL text
  entry before persistence. Plan:
  [checkpoint-remediation-r3-ingestion-boundaries.md](tasks/checkpoint-remediation-r3-ingestion-boundaries.md).
  **Local acceptance status (2026-09-12):** Completed. A path- and method-specific
  pure ASGI limiter now counts actual receive chunks before multipart parsing and
  caps the full request body at 5 MiB plus a tested fixed 64 KiB envelope. Direct
  ASGI coverage proves trusted-length early rejection, missing/malformed/
  conflicting/underreported length handling, multi-chunk stopping before the
  overflow chunk reaches downstream, one safe 413 response, non-target
  transparency, and room for a 5 MiB file with maximum legal metadata. Endpoint
  and Service file limits remain, and display-name, TXT, Markdown, per-page PDF,
  and final extracted-text paths reject U+0000 with a fixed 422 before Repository
  construction. The focused suite passed 40/40; guarded postgres-test migrations
  reached the single head with no drift, and the full non-provider integration
  selection passed 67/67, including no invalid row and a successful subsequent
  request. One later isolated-file rerun immediately after restarting the tmpfs-
  backed test service correctly failed because that restart had cleared every
  table; rerunning the migration guard and upgrade on the fresh instance restored
  the expected 12/12 knowledge-document pass. The ordinary suite passed 903 with
  68 integration/external cases deselected. Lock, Ruff, format, mypy, and
  staged/unstaged diff checks passed;
  no migration, dependency, commit, push, or remote GitHub Actions run occurred.
  The owner accepted R3 and instructed work to proceed to R4.
- **R4 — Deterministic prompt budget:** Budget the final serialized prompt across
  framing, goal/analysis, feedback, Projects, Tasks, and grounding; preserve
  latest/relevant data with deterministic Unicode-safe truncation and an explicit
  truncation manifest while retaining the 20,000-character schema limit. Plan:
  [checkpoint-remediation-r4-prompt-budget.md](tasks/checkpoint-remediation-r4-prompt-budget.md).
  **Local acceptance status (2026-09-13):** Completed. A pre-change maximum-
  schema reproduction reached `VersionedPrompt.input` with `string_too_long`.
  The final builder now applies centralized allocations of 1,000 framing/
  manifest, 4,000 goal/analysis, 1,100 feedback, 3,000 Projects, 5,000 Tasks,
  5,000 grounding, and an unallocated 900-character safety margin. It removes
  duplicated goal/analysis values, preserves stable identity/order before
  descriptions/excerpts, reclaims unused capacity in the fixed goal → Tasks →
  Projects → grounding order, and records every truncation/omission in a bounded
  manifest. Unicode tests cover CJK, combining marks, variation selectors, ZWJ,
  and long no-space input; JSON escape expansion is measured after serialization.
  The maximum combined legal Pydantic context produced the same 18,577-character
  input twice, leaving 1,423 characters below the unchanged 20,000 limit. The
  focused suite passed 32/32, the broader graph/approval/state/trace regression
  passed 85/85, and the ordinary suite passed 911 with 68 integration/external
  cases deselected. Lock, Ruff, format, mypy, and staged/unstaged diff checks
  passed. The trusted instructions, `study-plan.v2` output/fingerprint contract,
  dependencies, database schema, and migrations did not change. No Provider or
  network call, commit, push, or remote GitHub Actions run occurred. The owner
  accepted R4 and instructed work to proceed to R5.
- **R5 — Durable approval recovery:** Resolve the product-commit/checkpoint-resume
  crash window. The plan compares an outbox with an idempotent recovery protocol
  and selects the latter, using existing durable approval intent, run-scoped
  PostgreSQL locking, public checkpoint inspection, and existing Tool execution
  idempotency. Locked-version LangGraph continuation behavior is an implementation
  gate; failure stops for a renewed architecture decision. Plan:
  [checkpoint-remediation-r5-approval-recovery.md](tasks/checkpoint-remediation-r5-approval-recovery.md).
  **Local acceptance status (2026-09-13):** Completed. The implementation retained
  the selected idempotent recovery protocol instead of adding an outbox, migration,
  worker, or queue. Against LangGraph 1.2.11, langgraph-checkpoint 4.2.0, and
  langgraph-checkpoint-postgres 3.1.2, the workflow facade classifies only public
  `StateSnapshot` fields as pending interrupt, continuable, terminal, or
  inconsistent; durable advances use synchronous checkpoint durability, and a
  real interrupted graph proved public `invoke(None, config)` continuation. Each
  approval recovery holds a PostgreSQL transaction advisory lock derived
  deterministically from the complete run UUID on a dedicated connection, with
  collisions limited to safe over-serialization. Exact persisted decision,
  fingerprint, and normalized feedback retries recover or return the stored
  terminal snapshot; mismatches conflict, foreign ownership remains hidden, and
  inconsistent checkpoints fail closed without exposing raw state. PostgreSQL
  fault tests proved recovery after the decision commit and after checkpoint
  advancement, two competing same-run submissions, response-loss replay, and one
  high-impact domain write. The focused unit suite passed 63/63, the four-file
  recovery integration selection passed 9/9, the complete non-external-provider
  integration suite passed 71/71, and the ordinary suite passed 918 tests with 72
  cases deselected. The migration target guard passed; Alembic current and heads
  both reported `e3b7c2d9a410`, and check reported no new upgrade operations.
  Lock, Ruff, format, mypy, and staged/unstaged diff checks passed. Validation used
  only guarded `postgres-test`; no real Provider or remote network call, schema
  change, dependency change, commit, push, or remote GitHub Actions run occurred.
  The owner accepted R5 by instructing work to proceed to R6; no commit or push
  occurred.
- **R6 — Evaluation integrity:** Run synthetic stimuli through real prompt, graph,
  validation, Tool, grounding, approval, recovery, and idempotency paths with
  injected fakes; derive observations only from actual results and keep
  expectations solely for judgment. R6 follows R5 so recovery metrics evaluate
  the final protocol. Plan:
  [checkpoint-remediation-r6-evaluation-integrity.md](tasks/checkpoint-remediation-r6-evaluation-integrity.md).
  **Local acceptance status (2026-09-16):** Completed. A pre-change v1 mutation
  reproduced the defect: replacing a hostile case's input left its observation
  unchanged. The immutable v1 artifacts remain as explicitly historical evidence;
  the new `stage11-eval.v2`, `stage11-metrics.v2`, `stage11-gate.v2`, and
  `stage11-baseline.v2` contracts contain 30 synthetic cases whose input reaches
  production goal/prompt or owner-scoped retrieval/grounding. The Provider fake
  receives a real `ProviderRequest`; raw responses pass through production schema,
  graph, Tool argument, citation, and durable approval paths. Expectations are
  used only for final comparison, Provider usage comes from actual responses, and
  latency comes from injected clock calls. Reversing an expectation changed only
  judgment; changing the hostile delimiter input changed the observed boundary
  result and reduced its metric numerator from 3/3 to 2/3. Two independent fast
  runs produced byte-equivalent normalized reports and baselines; the committed
  30/30 baseline passed every predeclared contract threshold with zero unintended
  writes. `extraction_exact_match` was replaced by the accurately scoped
  `goal_propagation_accuracy`; recovery and duplicate claims were removed from the
  fast gate. Guarded PostgreSQL cases `recovery-post-commit` and
  `recovery-competing-high-impact` derive bounded observations from public
  checkpoint state and test-owned product/audit rows. The focused suite passed
  78/78, the complete non-external-provider integration suite passed 71/71, and
  the ordinary suite passed 928 tests with 72 cases deselected. The migration
  target guard passed; pre- and post-integration Alembic current/heads reported
  `e3b7c2d9a410`, and check found no new upgrade operations. Lock, Ruff, format,
  mypy, staged/unstaged diff, and bounded sensitive-pattern checks passed. A host
  AF_UNIX failure required preserving two Docker runtime directories as backups
  and restarting Windows; the Docker engine then recovered, the development named
  volume remained present, and only `postgres-test` was started and stopped. No
  real Provider/network call, dependency/schema change, commit, push, remote
  GitHub Actions run, independent re-review, or Task 12.7 work occurred.

The fixed gate order is:

```text
R1 → R2 → R3 → R4 → R5 → R6
→ independent pre-commit re-review
→ checkpoint commit
→ push
→ remote GitHub Actions
→ Task 12.7
```

Each remediation remains one explicit task with its own tests, quality gates,
report, and owner stop. No step may infer authorization for a later step.

MCP exposure of project/task capabilities and multi-agent experiments begin only
after Stage 12 acceptance. They are not part of the first Agent MVP.

### Post-Stage 12 enhancements

- Version 2 domain analytics add user-owned tags, non-overlapping study sessions,
  server-calculated actual duration, bounded statistics, and the timezone contract
  required for local-day reporting.
- MCP may expose already-stable project and task capabilities after its identity,
  authorization, and tool contracts have independent tests.
- Multi-agent experiments require a measured need that the explicit single-Agent
  graph cannot meet; they are not a default architecture goal.

### Unscheduled security backlog

- **P0 — 所有者作用域的有界审批 proposal 预览与 fingerprint 一致性验证**
  **Status:** Pending scheduling; not implemented. No formal Task number is
  assigned. This is an independent public-contract security improvement, not
  part of Task 12.7; existing task numbers and dependencies remain unchanged.
  Before implementation, confirm the public preview contract. Acceptance must
  prove all of the following with schema, service, and API tests:
  - Public fields use an explicit allowlist; preview item counts and total size
    have enforced bounds.
  - Trusted owner scope applies to every preview read, with cross-user isolation.
  - The preview describes the exact validated proposal that will be executed;
    no silent truncation or substitution can misrepresent an approved action.
  - Preview, approval, and execution agree on revision and fingerprint;
    stale revisions are rejected.
  - Sensitive fields are redacted or excluded; credentials, internal payloads,
    and complete private documents never enter the public preview.

## Roadmap consistency checklist

- Domain, first Agent MVP, deferred authentication, and post-MVP boundaries match
  `docs/requirements.md` and the governing brief.
- Layering, transactions, ownership, and technology choices match
  `docs/architecture.md` and `AGENTS.md`.
- Historical Stage 1–4 task contracts remain unchanged.
- Agent dependencies and directories appear only in their introducing task.
- Stage 5 is non-blocking; Stage 6 follows Stage 4 on the Agent MVP path.
- No roadmap statement claims a future Agent capability is already implemented.
