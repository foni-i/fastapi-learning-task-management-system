# Reproducible demo runbook

This runbook demonstrates the repository's implemented behavior with synthetic
data. The default path uses the local application and PostgreSQL but makes no
model or embedding request. Allow 10–15 minutes for a first run; the associated
screen recording is designed for 5–10 minutes.

The system and graph diagrams are in
[`docs/architecture.md`](../architecture.md). The synthetic upload fixture is
[`sample-syllabus.md`](sample-syllabus.md). Exact HTTP payloads are maintained in
the root [`README.md`](../../README.md#api-快速演示); this runbook intentionally
does not maintain a second copy.

## Safety contract

- Use only a local development checkout and the Compose `postgres-dev` service.
- Never point these commands at production or at a shared database.
- Do not run a migration downgrade, delete a volume, or use `docker compose down
  -v`.
- Do not put a password, access token, database URL, or Provider key in a file,
  command transcript, screenshot, or recording.
- The demo identity and content must be synthetic. Do not use a personal email or
  private document.
- Upload and indexing are separate. The default path may display the sample file
  but does not call the indexing endpoint.
- Citations identify retrieved sources; they do not prove that a statement is
  factually true.

The demo client still follows the production boundary:

```text
HTTP client → FastAPI Router/auth dependency → Schema → Service
            → owner-scoped Repository → synchronous SQLAlchemy → PostgreSQL
```

Agent evidence follows:

```text
Agent HTTP Service → LangGraph node → strict Agent Tool → Domain Service
                   → owner-scoped Repository → PostgreSQL
```

There is no demo authentication bypass, production fake Provider, direct SQL
seed, or approval bypass.

## 1. Preflight

From the repository root, confirm the checkout and tools without printing
environment values:

```powershell
git status --short
uv --version
uv run python --version
docker version
docker compose version
docker compose config --quiet
Test-Path docs/demo/sample-syllabus.md
```

Expected results:

- Python is 3.14.x and `uv` is available.
- Docker reports both Client and Server; Compose configuration exits 0.
- The synthetic syllabus exists.
- A dirty working tree is acceptable only when its changes are understood and
  preserved. Do not reset it for the demo.

Docker may be unavailable inside an automation sandbox even though Docker
Desktop works in ordinary PowerShell. Diagnose that as an environment boundary,
not as proof that Docker is absent.

## 2. Start the default environment

Generate a JWT signing secret only in the current PowerShell process, then build
and start `app` with its required development database:

```powershell
$env:STMS_ACCESS_TOKEN_SECRET = [Convert]::ToBase64String(
    [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
)
docker compose up -d --build --wait app
docker compose ps
```

Expected result: `app` and `postgres-dev` are `healthy`; `postgres-test` is not
started by this command. Compose runs `alembic upgrade head` before Uvicorn.

## 3. Show health and OpenAPI

```powershell
$api = "http://127.0.0.1:8000"
Invoke-RestMethod -Uri "$api/health/live"
Invoke-RestMethod -Uri "$api/health/ready"
Start-Process "$api/docs"
```

Both probes should return `status: ok`. In OpenAPI, point out the auth, user,
Project, Task, Agent-run, and knowledge-document routes. There is deliberately no
public knowledge-search route.

## 4. Run the authenticated domain walkthrough

Follow the root README's [API quick demo](../../README.md#api-快速演示) from the
credentials block through Task creation. Use its generated/current-process
variables; replace the placeholder email with a unique synthetic `example.com`
address if the documented account already exists.

The visible milestones are:

1. registration returns HTTP 201;
2. login returns HTTP 200 without displaying the access token;
3. `GET /api/v1/users/me` returns the four-field public user view;
4. Project creation returns HTTP 201 without `user_id`;
5. Task creation returns HTTP 201 and binds the Task to the owned Project.

Do not show the PowerShell variable that contains the token. The payload never
contains a caller-selected `user_id`, Session, SQL, or database connection.

The default walkthrough may display `docs/demo/sample-syllabus.md` and the
knowledge upload route. If it uploads the file, stop after the PARSED metadata
response. Do not invoke `/index` without separate Provider authorization.

## 5. Show Agent, RAG, and approval evidence offline

The default demo does not attach a fake Provider to the running application.
Instead, show the graph in `docs/architecture.md`, the strict public Agent routes
in OpenAPI, and execute deterministic evidence:

```powershell
uv run pytest -q `
    tests/test_agent_run_api.py `
    tests/test_agent_graph.py `
    tests/test_agent_grounding.py `
    tests/test_hybrid_retrieval.py

uv run pytest -q `
    tests/test_agent_evaluation.py `
    tests/test_agent_evaluation_metrics.py
```

Explain these boundaries while the results are visible:

- `load_context` uses the owner-scoped `search_knowledge` Tool;
- lexical and vector candidates are independently bounded and filtered by owner
  before ranking;
- fixed reciprocal-rank fusion is deterministic;
- retrieved excerpts remain bounded untrusted data;
- citation validation occurs before the proposal fingerprint and approval;
- high-impact writes require exact approval and a durable idempotency claim.

The current `stage11-baseline.v2` contains 30 synthetic cases and a passing fast
contract gate. Input reaches the production goal/prompt or retrieval/grounding
path, while expectations are used only for final judgment. The v1 artifacts stay
available as history but self-attested several observations and are not current
accuracy evidence. Neither version measures real-model correctness, factual
grounding, production latency, or an SLA. Recovery/duplicate evidence comes from
the separate guarded PostgreSQL integration run, not the fast baseline.

## 6. Optional real Provider segment

Skip this section by default. Run it only when the owner separately authorizes:

- temporary Provider credentials;
- outbound network access;
- possible API charges;
- use of synthetic, non-sensitive prompts and documents.

Provide credentials through the current process or an uncommitted `.env`. Pause
recording while entering them. Never display settings, environment dumps,
Authorization headers, complete prompts, model responses, document contents, or
hidden reasoning. Run only the explicit index or Agent request selected by the
owner, then remove the Provider variables from the process.

An unavailable Provider is not a failure of the default demo; it is an optional
integration boundary.

## 7. Stop safely

Stop only the services started by this runbook and preserve the development
named volume:

```powershell
docker compose stop app postgres-dev
Remove-Item Env:STMS_ACCESS_TOKEN_SECRET -ErrorAction SilentlyContinue
Remove-Item Env:STMS_DEMO_ACCESS_TOKEN -ErrorAction SilentlyContinue
Remove-Variable login, headers, credentials -ErrorAction SilentlyContinue
```

Do not use `docker compose down -v`. The synthetic demo account, Project, and
Task remain in the local development volume; this runbook intentionally provides
no destructive reset or direct-database cleanup command.

## Evidence and recording

- The latest bounded local snapshot is in [`results.md`](results.md).
- The 5–10 minute shot list is in
  [`recording-checklist.md`](recording-checklist.md).
- Remote GitHub Actions evidence is unavailable until Task 12.2 is committed,
  pushed, and a matching workflow run is observed. Do not add a green badge based
  only on local validation.
