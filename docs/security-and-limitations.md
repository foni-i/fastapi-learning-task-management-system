# Security, failure modes, limitations, and cost boundaries

## Status and interpretation

This document describes the repository at Alembic head `e3b7c2d9a410`, including
checkpoint commit `b05adae` and the documentation audit on 2026-09-16. It is an
engineering disclosure for a learning project, not a security
certification, privacy policy, compliance statement, production-readiness claim,
or warranty.

Claims are classified as:

- **Proved control** — directly exercised by an automated test, database
  constraint, or the bounded Task 12.5 execution snapshot.
- **Best-effort boundary** — implemented, but incomplete against the full threat
  or failure space.
- **Not guaranteed** — explicitly outside the evidence and current design.

The current architecture and data flows are documented in
[`architecture.md`](architecture.md). Reproduction commands and bounded local
results are in [`demo/README.md`](demo/README.md) and
[`demo/results.md`](demo/results.md).

## Failure-mode register

| Area and trigger | User-visible behavior | Current control and evidence | Residual risk | Prioritized improvement |
| --- | --- | --- | --- | --- |
| **Configuration/startup:** database URL is absent or malformed | Readiness returns a bounded unavailable result; database-backed operations cannot succeed | Secret-aware validation in [`app/core/config.py`](../app/core/config.py), readiness translation in [`app/api/health.py`](../app/api/health.py), tests in [`tests/test_config.py`](../tests/test_config.py) and [`tests/test_health.py`](../tests/test_health.py) | Liveness can be healthy while the product is unusable; there is no centralized startup configuration report | **P1 proposal:** add a non-secret startup validation summary and deployment checklist |
| **Container migration:** Alembic upgrade fails before Uvicorn | App container exits instead of serving against an unknown schema | Fail-fast command in [`Dockerfile`](../Dockerfile), Compose health dependency in [`compose.yaml`](../compose.yaml), and asset tests in [`tests/test_docker_assets.py`](../tests/test_docker_assets.py) | No multi-replica migration coordination, rollback automation, or production release strategy | **P0 proposal before cloud deployment:** separate migration job, release lock, rollback/restore procedure |
| **Database outage or pool exhaustion** | Readiness becomes 503; affected API/Agent operations fail | Short synchronous Session lifecycle in [`app/db/session.py`](../app/db/session.py), bounded readiness probe, and real database tests under [`tests/integration/`](../tests/integration/) | No high-availability database, circuit breaker, capacity test, backup, or recovery-time evidence | **P0 proposal before production:** backup/restore drill, connection/pool sizing, availability design |
| **JWT secret missing, weak, or changed** | Token issuance/validation fails safely; existing tokens become invalid after a secret change | Minimum 32-character configured secret and fixed HS256/issuer/audience/type/expiry validation in [`app/core/config.py`](../app/core/config.py) and [`app/core/tokens.py`](../app/core/tokens.py); [`tests/test_access_tokens.py`](../tests/test_access_tokens.py) | No secret manager, rotation procedure, key identifier, asymmetric signing, or overlap window | **P0 proposal before production:** managed secret storage and documented rotation policy |
| **Credential guessing or stolen access token** | Wrong and unknown credentials share a generic 401; a valid stolen token works until expiry | Argon2id password storage in [`app/core/security.py`](../app/core/security.py), generic auth errors, short token TTL, and [`tests/integration/test_authentication.py`](../tests/integration/test_authentication.py) | No rate limiting, MFA, refresh-token revocation, logout, password-change revocation, or anomaly detection | **P0 roadmap:** complete deferred Stage 5; **P0 proposal:** rate limiting and abuse monitoring |
| **Cross-user resource request** | Missing and foreign-owned resources normally share the same safe 404 | Trusted identity dependency, owner predicates in [`app/repositories/`](../app/repositories/), composite ownership constraints, and Project/Task/Agent/RAG integration tests | A compromised owner token still grants that owner's access; there is no role model or tenant administration | Keep single-owner scope; add roles only after a concrete product requirement |
| **Application write fails after mutation begins** | Known domain failures are bounded; the transaction is rolled back | Services own commit/rollback, Repositories only flush; examples in [`app/services/tasks.py`](../app/services/tasks.py), [`app/services/knowledge_document_indexing.py`](../app/services/knowledge_document_indexing.py), and transaction tests | External Provider work and its cost cannot be rolled back; process loss around distributed boundaries can leave an operation needing reconciliation | **P1 proposal:** explicit operation state/reconciliation for external side effects |
| **Unexpected exception while debug mode is enabled** | Development responses may expose more diagnostics than production-safe errors | Password-bearing validation input is masked in [`app/main.py`](../app/main.py); known domain/provider errors use bounded messages | No global production error envelope, request-ID middleware, structured redaction policy, or proof for every unexpected exception; `STMS_DEBUG=true` is unsafe for public deployment | **P0 proposal before public exposure:** force debug off, global safe handler, request IDs, redacted structured logging |
| **Model or Embedding Provider is absent, slow, rate-limited, or malformed** | Agent/index/search returns a bounded unavailable/configuration failure | Replaceable Protocols, timeouts, bounded retries for planning, fixed embedding validation, and fakes in [`app/agent/providers.py`](../app/agent/providers.py), [`app/agent/embeddings.py`](../app/agent/embeddings.py), and their tests | No circuit breaker, provider failover, quota monitor, real-service SLO, or cost ceiling; hybrid retrieval currently requires query embedding, so Provider failure also loses lexical results | **P1 proposal:** explicit budgets and circuit breaker; decide and test whether lexical-only degradation is acceptable |
| **Combined legal goal, Project, Task, feedback, and grounding data exceeds the planning input schema** | The Provider receives a deterministic bounded projection rather than an input-validation failure | Central 20,000-character budget, 900-character final reserve, stable priority projection, Unicode-safe truncation, and explicit omission manifest in [`app/agent/prompt_budget.py`](../app/agent/prompt_budget.py); maximum-schema tests in [`tests/test_agent_prompts.py`](../tests/test_agent_prompts.py) | Character limits are not token or monetary budgets; truncation can remove context and reduce model quality even when identity and ordering are preserved | Measure real token use and plan quality against representative workloads; adjust only through a versioned, tested budget decision |
| **Model requests an unknown or forbidden Tool/argument** | Request fails with a fixed “not allowed” or “invalid” classification before a gateway opens | Exact allowlist and `extra="forbid"` schemas in [`app/agent/tools.py`](../app/agent/tools.py); policy tests in [`tests/test_agent_high_impact_policy.py`](../tests/test_agent_high_impact_policy.py) | An allowed action can still be semantically poor; schemas do not establish user intent | Continue deterministic validation and require meaningful human review for writes |
| **Approval is stale, forged, repeated, or bypassed** | The owner can inspect the complete typed pending proposal before deciding; exact decided submissions recover or return the existing terminal snapshot; any owner/revision/fingerprint/decision/feedback mismatch conflicts before resume | Owner-scoped `GET .../approval-preview`, 65,536-byte fail-closed projection, canonical fingerprint round-trip, persisted decision, run-scoped transaction lock, public checkpoint inspection, and unique execution claim in [`app/agent/nodes/approval.py`](../app/agent/nodes/approval.py), [`app/services/agent_workflow.py`](../app/services/agent_workflow.py), [`app/repositories/agent_recovery.py`](../app/repositories/agent_recovery.py), and [`app/services/agent_tool_executions.py`](../app/services/agent_tool_executions.py) | The API supplies inspectable data but no dedicated approval UI or independent semantic-quality guarantee; a valid owner can still approve a poor plan, and preview availability depends on PostgreSQL/checkpoint availability | Build approval UX only from this typed endpoint; retain human judgment, optimistic stale rejection, and operational checkpoint monitoring |
| **Process stops after approval commit, during checkpoint progress, or after product completion** | The same exact request serializes per Run, inspects the public checkpoint state, resumes/continues only when supported, or reconciles terminal product state; unknown state returns fixed 503 and remains retryable | LangGraph 1.2.11 public snapshot/continuation, transaction-scoped PostgreSQL advisory lock, persisted approval intent, Tool replay/UNKNOWN controls, and fault/competition tests in [`tests/integration/test_agent_approval_recovery.py`](../tests/integration/test_agent_approval_recovery.py) | Recovery occupies one additional pooled database connection for the full synchronous graph advance; PostgreSQL/checkpoint outage still blocks recovery, hash collisions may serialize unrelated Runs, and UNKNOWN external outcomes still require manual reconciliation | Size the pool for concurrent recovery, add an operational reconciliation view/runbook, and consider an outbox worker only when asynchronous recovery is a measured requirement |
| **Hostile or false retrieved document** | Text remains bounded evidence data; illegal citation references fail validation before approval | Owner-filter-before-rank, fixed RRF, `knowledge-grounding.v1` delimiter/escaping, citation allowlist, and [`tests/test_agent_grounding.py`](../tests/test_agent_grounding.py) | Prompt injection defenses reduce control-flow escape but cannot make content true, complete, unbiased, or non-malicious; citations identify sources only | **P1 proposal:** provenance UX and claim-level factual verification; retain human review |
| **File is oversized, unsupported, encrypted, malformed, PostgreSQL-incompatible, or expensive to parse** | Upload returns bounded 413/422 errors and closes the upload | Pure ASGI chunk counting caps the pre-parser multipart body at 5 MiB + 64 KiB; endpoint/Service retain the 5 MiB file limit; page/text limits and strict media/extension checks remain; U+0000 is rejected from display names and every extracted-text path before persistence. Covered by [`tests/test_request_body_limit.py`](../tests/test_request_body_limit.py), [`tests/test_knowledge_document_api.py`](../tests/test_knowledge_document_api.py), and PostgreSQL integration tests | A proxy or server may buffer before ASGI receives data. No antivirus, content-disarm, parser sandbox, OCR, signature verification, or protection against an unknown parser vulnerability | **P0 proposal before untrusted public uploads:** edge request limits plus isolated scanning/parsing service and dependency vulnerability process |
| **SSE reconnect uses an invalid or no-longer-retained cursor** | Request fails with bounded 409; valid cursor resumes after the exact retained event | Stable bounded event projection in [`app/services/agent_events.py`](../app/services/agent_events.py) and [`tests/test_agent_sse.py`](../tests/test_agent_sse.py) | This is a database-derived snapshot, not a durable event broker; retention is bounded and there is no delivery/SLA guarantee | **P2 proposal:** document retention and reconnect semantics; add a broker only if measured requirements justify it |
| **Trace sink fails or receives unsafe content** | Sink failure is ignored and cannot change workflow outcome | Strict `agent-trace.v1` allowlist, metadata-only events, default no-op sink, and [`tests/test_agent_tracing.py`](../tests/test_agent_tracing.py) | Default tracing retains nothing, so production diagnosis is limited; no exporter, access policy, sampling, or retention proof | **P1 proposal:** privacy-reviewed exporter with access/retention controls and redaction tests |
| **CI dependency/service failure or unsafe test target** | Job fails; migration guard rejects a non-dedicated target before migration | Locked dependencies, pinned action revisions, dedicated pgvector service, explicit target validator in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) and [`tests/integration/conftest.py`](../tests/integration/conftest.py), plus successful [run 35097448961](https://github.com/foni-i/fastapi-learning-task-management-system/actions/runs/35097448961) for checkpoint `b05adae` | One successful run is not an availability history; no SAST, dependency review, image scan, secret scan, provenance, or branch-protection evidence | **P1 proposal:** add supply-chain/security checks based on threat model and monitor repeated CI behavior |
| **Synthetic evaluation passes but real behavior regresses** | Offline gate remains deterministic and may not detect real Provider or traffic-distribution failures | R6 `stage11-eval.v2` runs 30 stimuli through production goal/prompt, Provider contract, graph, Tool/citation validators, retrieval/grounding, and approval boundaries; expectations are judgment-only, mutation tests change real observations/metrics, and recovery evidence is a separate guarded PostgreSQL integration subset | Synthetic Provider/retrieval data still cannot establish model correctness, relevance, factuality, production latency/cost, multilingual breadth, load behavior, or an adversarial-parser guarantee; v1 is historical and self-attested several observations | **P1 proposal:** versioned real-model/human eval run outside ordinary CI with explicit privacy and cost approval |

## Security statement

### Local Compose network boundary

The default published ports for app, postgres-dev, and postgres-test bind to
`127.0.0.1`. The three existing port variables change only the host port.
Uvicorn still listens on `0.0.0.0:8000` inside its container, and the app reaches
`postgres-dev:5432` through Compose DNS. These internal addresses do not widen
the host publish scope. An operator must supply an explicit additional `-f`
Compose override for remote exposure and provide firewall rules, TLS, and
non-example credentials. Loopback binding does not protect against local
processes or host compromise. Existing containers retain their old bindings
until their port configuration is reapplied; restarting them is insufficient.
Parsed default/overridden port contracts are checked by
[`tests/test_docker_assets.py`](../tests/test_docker_assets.py) using the Docker
Compose CLI without contacting the daemon. These two checks skip when the CLI
is absent; R2 acceptance requires an actual CLI run and runtime inspection.

### Proved controls

Within the tested repository boundary, the following statements have direct
evidence:

1. Registration hashes accepted passwords with Argon2id; public user schemas do
   not contain password hashes. Access tokens use a configured secret and validate
   a fixed algorithm, expiry, token type, issuer, audience, and UUID subject.
2. Project, Task, Agent product-record, document, chunk, and retrieval queries are
   owner-scoped. Clients cannot select `user_id` through public schemas or Agent
   Tool arguments. Composite database constraints provide a second ownership
   boundary where cross-table ownership matters.
3. Domain write Services own commit/rollback; Repositories do not commit.
4. Agent Tool names and arguments are strictly allowlisted. Cited proposals are
   validated before fingerprinting and approval. High-impact writes require a
   matching persisted approval and a unique idempotency claim. Approval recovery
   is serialized per Run and never retries an `UNKNOWN` Tool outcome.
5. Hybrid retrieval uses bounded owner-filtered lexical and vector candidates,
   deterministic RRF, bounded public citations, and a versioned untrusted-data
   grounding boundary.
6. Agent tracing is metadata-only and bounded; its default sink retains nothing,
   and sink failure is isolated from business behavior.
7. The protected integration harness rejects unsafe migration targets. Task 12.5
   demonstrated a real local Compose startup and owner-scoped domain flow while
   preserving the development volume.

Evidence is distributed across the linked code/tests above and the dated
[`demo/results.md`](demo/results.md) snapshot. “Proved” means those exact tested
properties, not absence of all vulnerabilities.

### Best-effort boundaries

- Known authentication, domain, Provider, Agent, document, and retrieval failures
  are translated to fixed or bounded errors. Unexpected exceptions are not yet
  covered by one production-grade global sanitization and logging policy.
- Input bounds, parser limits, and untrusted grounding reduce exposure; they are
  not malware detection, parser isolation, or factual verification.
- The deterministic planning-input budget prevents schema overflow, but does not
  guarantee a model-token ceiling or that omitted descriptions were unimportant.
- Approval fingerprinting and idempotency protect technical execution identity;
  the current public API does not yet provide a sufficient proposal-preview
  contract for strong informed-consent claims.
- Checkpoints, run-scoped locks, and execution claims support tested recovery
  scenarios; they do not provide distributed exactly-once execution, make
  PostgreSQL highly available, or resolve every uncertain external outcome.
- Local Compose and one successful checkpoint workflow are reproducible engineering
  evidence, not evidence of a hardened production deployment or sustained CI reliability.

### Explicit non-guarantees

The project does not guarantee:

- production availability, throughput, latency, durability, backup recovery, or
  disaster recovery objectives;
- protection against denial of service, brute force, host compromise, database
  compromise, supply-chain compromise, or unknown parser/model vulnerabilities;
- logout revocation, Refresh Token rotation, password-change revocation, MFA,
  account recovery, roles, or administrative controls;
- correctness, truthfulness, completeness, neutrality, or safety of uploaded
  documents, model output, retrieved excerpts, plans, or citations;
- real-Provider quality, deterministic output, pricing, quota availability, data
  residency, or production token/latency characteristics;
- live distributed tracing, alerting, audit immutability, event delivery, legal
  compliance, or privacy certification;
- safe public Internet deployment without additional TLS, reverse-proxy, secret,
  logging, rate-limit, scanning, monitoring, and backup controls.

## Prioritized improvement route

This list documents dependencies; it does not authorize implementation or change
the roadmap.

| Priority | Improvement | Dependency and classification |
| --- | --- | --- |
| P0 | Complete Refresh Token rotation, logout, password change, and revocation | Existing deferred Stage 5 roadmap |
| P0 | Add production edge and operations controls: TLS termination, debug-off enforcement, managed secrets, safe global errors/logging, rate limiting, backup/restore drill | **Proposal** required before public deployment; each high-risk control should be a separate task |
| P0 | Isolate and scan untrusted file parsing | **Proposal** required before accepting public untrusted uploads |
| P1 | Add privacy-reviewed trace export, alerting, retention, and access controls | **Proposal**; depends on a data-classification and observability decision |
| P1 | Add human-rated and real-Provider evaluation for relevance, factuality, multilingual behavior, drift, latency, and cost | **Proposal**; requires explicit data, network, credential, and budget approval |
| P1 | Define lexical-only degradation, Provider budgets, circuit breaking, and external-side-effect reconciliation | **Proposal**; requires product choices and failure-contract tests |
| P2 | Implement tags, study sessions, and bounded analytics | Existing post-Stage 12 Version 2 roadmap |
| P2 | Expose stable capabilities through MCP | Existing post-Stage 12 roadmap; requires independent identity and authorization contracts |
| P3 | Evaluate multi-Agent orchestration only after measured single-Agent limitations | Existing post-Stage 12 condition, not a default architecture goal |

## Cost model and capacity boundaries

No supplier price is quoted here. Prices, free tiers, currencies, model IDs, and
CI billing rules change; an actual estimate must use the official current price
at the time of a separately authorized deployment decision.

### Fixed and local costs

- **Developer machine:** Docker image download/build CPU, dependency cache,
  application memory, PostgreSQL memory, and local network/port use.
- **Persistent disk:** the `postgres-dev` named volume, application image layers,
  uv cache, source checkout, documents, chunks, embeddings, database indexes,
  Agent product records, and LangGraph checkpoints.
- **Test isolation:** `postgres-test` uses memory-backed `tmpfs` while running and
  consumes CPU/memory but not the development volume.

These were functionally exercised in Task 12.5, but CPU, peak memory, image size,
database growth, and build-cache growth were not benchmarked.

### Variable Provider costs

For a model whose prices are quoted per token unit:

```text
model_cost =
    total_input_tokens  × input_unit_price
  + total_output_tokens × output_unit_price
```

Actual totals include every initial attempt and retry. Prompt instructions,
bounded project/task context, grounding excerpts, schema overhead, and model
output all contribute. The configured retry bound limits attempts but does not
make the spend zero.

The final serialized planning `input` is limited to 19,100 characters, retaining
900 characters below its 20,000-character schema ceiling. This is a reliability
boundary, not a tokenizer-based price cap: trusted instructions, output-schema
serialization, provider tokenization, response tokens, and retries still affect
actual usage and cost.

Embedding cost follows the Provider's current unit:

```text
embedding_cost =
    indexed_document_text_units × embedding_unit_price
  + search_query_text_units     × embedding_unit_price
```

Re-indexing replaces stored chunks atomically but may repeat paid embedding work.
Every `search_knowledge` call currently creates one query embedding before both
retrieval branches. Database rollback cannot refund completed Provider requests.

The Stage 11 synthetic token and latency metrics must not be inserted into these
formulas as a production forecast.

### CI and storage costs

```text
ci_usage = quality_job_minutes + integration_job_minutes
```

Billing depends on the repository plan, runner, concurrency, cache behavior, and
current platform terms. Checkpoint run 35097448961 measured 1m02s for quality and
47s for integration, but one run is not a stable duration or billing forecast.

Knowledge storage grows approximately with document text, chunk count, 1536
vector values per chunk, GIN/HNSW index overhead, and PostgreSQL maintenance
overhead. Agent storage grows with runs, approvals, Tool execution records, and
checkpoint history. The default TraceSink adds no external trace storage; a
future exporter would add event-volume × retention and vendor-ingestion costs.

### Unknowns requiring measurement

- concurrent request throughput and synchronous worker requirements;
- Provider latency distribution, retries, rate limits, and token use;
- real document chunk/index size and HNSW build/query behavior;
- checkpoint retention and database growth;
- backup size, restore time, log retention, trace volume, and alerting cost;
- CI duration distribution, cache behavior, and billed usage across repeated runs.

These values require a defined workload and an authorized measurement plan; they
cannot be inferred from the 30-case v2 synthetic contract baseline.

## Evidence snapshot

Task 12.5 recorded:

- Python 3.14.7, Docker Engine 29.7.2, Compose 5.4.0, PostgreSQL 17.11;
- healthy Compose app/database startup and the public synthetic owner-scoped flow;
- 48 passing Agent/RAG evidence tests and 28 passing offline-evaluation tests;
- 884 passing ordinary tests with 67 integration/external cases deselected;
- a 39/39 synthetic evaluation baseline and passing predeclared gate;
- no real Provider call, destructive migration, or volume removal; remote CI was
  not yet available at the time of that historical snapshot.

These facts are reproducible through [`demo/README.md`](demo/README.md). They do
not extend the guarantees beyond the exact environments and tests described.

R6 supersedes only the evaluation interpretation: its 30/30 v2 fast contract
baseline passed twice with identical normalized output; a hostile-input mutation
changed an actual grounding observation and metric; and 71/71 guarded PostgreSQL
integration tests supplied the separate recovery/idempotency evidence. The full
ordinary suite passed 928 tests with 72 integration/external cases deselected.
The v1 39-case result above remains a Task 12.5 historical snapshot, not current
accuracy or recovery evidence.

The later checkpoint commit `b05adae` has one matching successful remote run,
[35097448961](https://github.com/foni-i/fastapi-learning-task-management-system/actions/runs/35097448961),
whose quality and PostgreSQL integration jobs both passed. Later uncommitted
documentation changes are not covered by that run.
