# 5–10 minute recording checklist

Use synthetic data and keep terminals zoomed so commands and bounded results are
readable. Close unrelated applications and notifications before recording.

## Before recording

- [ ] Confirm the browser and terminal do not expose `.env`, tokens, passwords,
      database URLs, Provider keys, personal email, or private documents.
- [ ] Start the environment using `docs/demo/README.md`; verify `app` and
      `postgres-dev` are healthy.
- [ ] Prepare a unique synthetic `example.com` account in current-process
      variables without printing its password or token.
- [ ] Open the README, architecture document, Swagger UI, and bounded results
      snapshot in separate tabs.
- [ ] Keep the optional real-Provider segment excluded unless separately
      authorized.

## Shot list

| Time | Screen | Narration and visible evidence |
| --- | --- | --- |
| 0:00–0:45 | Root README | State the product goal: an owner-isolated study-task API with a bounded, approval-gated single Agent and focused RAG. State that this is a modular monolith. |
| 0:45–1:30 | Architecture deployment and HTTP diagrams | Show client → FastAPI → Service → Repository → PostgreSQL and the isolated development/test database boundaries. |
| 1:30–2:15 | Terminal and Swagger health routes | Show the one-command Compose startup summary, `/health/live`, `/health/ready`, and OpenAPI. Do not show environment values. |
| 2:15–3:30 | Swagger or sanitized terminal | Register/login without displaying the token, then show current-user, Project creation, and Task creation public responses. Point out the absence of `user_id` and password fields. |
| 3:30–4:15 | Synthetic syllabus and knowledge routes | Show that the document is synthetic and untrusted. Explain that upload and explicit indexing are separate and skip real embedding by default. |
| 4:15–5:30 | Eight-node LangGraph diagram | Explain context loading, plan generation/validation, durable approval interrupt/resume, revision loop, verification, and summary. Emphasize that only an exact approved fingerprint reaches writes. |
| 5:30–6:30 | RAG/grounding and persistence diagrams | Show owner-filter-before-rank, fixed RRF, bounded excerpts, citation validation, and separation of product audit, checkpoint, SSE, and tracing. |
| 6:30–7:30 | Focused test output and `results.md` | Show deterministic Agent/RAG tests and the 30-case Stage 11 v2 contract baseline. Note that v1 is historical/self-attesting, PostgreSQL recovery evidence is separate, and fake metrics are not production accuracy, latency, truth, or cost. |
| 7:30–8:15 | Current limits and safe stop | Note missing refresh/logout/password change, fact verification, tracing exporter, public search, MCP, multi-Agent, and cloud deployment. Show the non-destructive stop command. |

## Narration guardrails

- Say “citation identifies a source,” not “citation proves the claim.”
- Say “offline deterministic baseline,” not “model accuracy” or “production SLA.”
- Say “locally verified workflow; remote run unavailable,” not “GitHub CI passed.”
- Say “optional Provider integration,” not “the default demo calls OpenAI.”
- Do not describe retrieved document text as trusted policy or executable
  instructions.

## After recording

- [ ] Stop only `app` and `postgres-dev`; preserve the named volume.
- [ ] Remove JWT, demo-token, and any optional Provider variables from the current
      process.
- [ ] Review the recording frame-by-frame for secrets, tokens, personal data,
      private documents, full prompts/model responses, SQL, vectors, or hidden
      reasoning before publishing.
- [ ] Do not upload or publish the recording without the owner's explicit choice
      of destination and authorization.
