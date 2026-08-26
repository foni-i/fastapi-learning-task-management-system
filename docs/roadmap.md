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
Docker is unavailable on `PATH`. Replace database URL placeholders locally and
never paste credentials into command output, logs, tests, or commits.

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
