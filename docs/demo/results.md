# Local demo and verification snapshot

Recorded on 2026-09-07 in an ordinary Windows PowerShell host environment.
The repository was on branch `main` at commit `ed2d1dd`, with understood
uncommitted changes from Stage 11 and Tasks 12.1–12.5. No commit or push was
performed for this snapshot.

## Environment

| Component | Observed value |
| --- | --- |
| Host Python through uv | 3.14.7 |
| App-container Python | 3.14.7 |
| uv | 0.12.5 |
| Docker Client / Engine | 29.7.2 / 29.7.2 |
| Docker Desktop | 4.87.0 |
| Docker context | `desktop-linux` |
| Docker Compose | 5.4.0 |
| Development database | PostgreSQL 17.11, pgvector image `0.8.6-pg17-bookworm` |
| Alembic head expected by the repository | `e3b7c2d9a410` |

The Codex sandbox did not expose `uv` or Docker on its PATH. The same commands
were therefore run in the ordinary host PowerShell environment, where both
Docker Client and Server were available. This is a sandbox-access boundary, not
an absent Docker installation.

## Default demo execution

| Command or check | Exit/status | Bounded result |
| --- | --- | --- |
| `docker compose config --quiet` | 0 | Compose configuration valid |
| `docker compose up -d --build --wait app` | 0 | Image built; `app` and `postgres-dev` healthy |
| `GET /health/live` | HTTP 200 | `status: ok` |
| `GET /health/ready` | HTTP 200 | PostgreSQL ready |
| `GET /openapi.json` | HTTP 200 | OpenAPI available |
| synthetic registration | HTTP 201 | Public user created; credentials not recorded |
| synthetic login | HTTP 200 | Token retained only in the process and not printed |
| `GET /api/v1/users/me` | HTTP 200 | Owner-scoped public user returned |
| synthetic Project creation | HTTP 201 | Public Project returned without owner ID |
| synthetic Task creation | HTTP 201 | Task created under the owned Project |

The run did not call the document-index endpoint, Agent Provider, Embedding
Provider, external network model API, `postgres-test`, migration downgrade, or a
direct database write. It did not print or save the generated JWT secret,
password, access token, synthetic account address, resource UUIDs, or database
URL. The development named volume was preserved.

## Stage 11 R6 offline contract baseline

Source: `evals/stage11/baseline.v2.json`. The v1 artifact remains historical and
is not current behavior evidence because it copied several final observations
from its fake script.

| Evidence | Recorded value |
| --- | --- |
| Baseline / dataset / metrics / gate versions | `stage11-baseline.v2` / `stage11-eval.v2` / `stage11-metrics.v2` / `stage11-gate.v2` |
| Total cases | 30 |
| Succeeded / failed / malformed | 30 / 0 / 0 |
| Goal propagation contract | 3 / 3 (1.0) |
| Tool contract handling | 9 / 9 (1.0) |
| Citation contract handling | 4 / 4 (1.0) |
| Plan contract handling | 4 / 4 (1.0) |
| Hostile-input boundary integrity | 3 / 3 (1.0) |
| Approval boundary handling | 3 / 3 (1.0) |
| Safe error contract | 2 / 2 (1.0) |
| Unintended writes | 0 |
| Injected-clock latency p50 / p95 | 0.0 ms / 0.0 ms |
| Known Provider usage | 1 case; 120 input / 30 output / 150 total tokens |
| PostgreSQL evidence IDs | `recovery-post-commit`, `recovery-competing-high-impact` (separate integration run) |
| Gate | passed |

These are deterministic fake contract results. They do not measure real-model
accuracy, retrieval truth, factual correctness, network latency, availability,
production cost, or an SLA. The baseline stores safe case IDs and aggregates
rather than complete input, prompts, document text, model output, Tool arguments,
Tokens, database URLs, or credentials.

## Local test and quality evidence

| Command | Exit | Summary |
| --- | --- | --- |
| R6 focused evaluation/graph/grounding/policy tests | 0 | 78 passed in 8.06 s |
| Two independent v2 fast runs | 0 | normalized report equal; normalized baseline equal; 30/30; gate passed |
| Guarded non-external-provider integration suite | 0 | 71 passed in 20.16 s |
| Alembic target/current/heads/check | 0 | guarded target; `e3b7c2d9a410` current and sole head; no new operations |
| `uv run pytest -q` | 0 | 928 passed, 72 deselected in 14.85 s |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 247 files already formatted |
| `uv run mypy app tests alembic` | 0 | No issues in 223 source files |
| `uv lock --check` | 0 | Resolved 98 locked packages |

R6 started and stopped only `postgres-test`. A final read-only check showed all
three Compose services exited and `fastapi-stms-postgres-dev-data` still present.

At the time of the 2026-09-07 demo snapshot, remote CI evidence was unavailable
because the Task 12.2 workflow changes had not yet been committed or pushed.
No real Provider or external model network was used.

## Checkpoint and remote CI update

On 2026-09-16, the Stage 11 through checkpoint-remediation work was committed as
`b05adae` and pushed to `origin/main`. Matching
[GitHub Actions run 35097448961](https://github.com/foni-i/fastapi-learning-task-management-system/actions/runs/35097448961)
completed successfully: `Offline tests and quality` succeeded in 1m02s and
`PostgreSQL integration and migrations` succeeded in 47s. This remote result
applies to that exact checkpoint commit, not to later uncommitted documentation.
It is CI evidence, not production deployment, real-Provider, or
SLA evidence.
