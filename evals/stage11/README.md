# Stage 11 offline evaluation datasets

`dataset.v1.jsonl` and `baseline.v1.json` remain immutable historical artifacts.
Their `fake_script` supplied final approval, recovery, duplicate, write, token,
and latency observations, while `EvaluationInput.text` did not affect execution.
They therefore demonstrate the former fixture contract and reproducibility, not
independent behavior measurement. Do not use the v1 accuracy or recovery names
as current release evidence.

R6 adds the separate `stage11-eval.v2` dataset and `stage11-baseline.v2`. Its 30
synthetic cases contain input, dependency stimuli, and independent expectations.
The expectation is available only after the observation is complete. Fakes
receive a scenario stimulus, never a case ID or expected observation.

The fast runner sends input through the production goal analysis, prompt builder,
Provider request/response schema, graph, Tool argument validator, owner-scoped
knowledge retrieval Service, grounding renderer, citation validator, and durable
approval interrupt/resume path. Provider usage comes from actual
`ProviderResponse.usage`; latency comes from calls to an injected clock. This path
uses no database, network, credential, sleep, or real write.

Run the fast evidence through the normal suite:

```powershell
uv run pytest -q tests/test_agent_evaluation.py tests/test_agent_evaluation_metrics.py
```

The v2 report and baseline contain only stable case IDs, categories, safe error
codes, booleans, bounded counters, aggregate metrics, and thresholds. They exclude
complete input, prompts, retrieved text, Tool arguments/results, model output,
credentials, Tokens, database URLs, exception text, and hidden reasoning.

`stage11-metrics.v2` deliberately names only observed contracts:

- goal propagation (3 cases);
- Tool-name/argument contract handling (9 cases);
- citation contract handling (4 cases);
- plan-schema/bounds handling (4 cases);
- hostile-input boundary integrity (3 cases);
- approval boundary handling (3 cases);
- safe error classification (2 cases).

The gate requires each applicable contract rate to equal 1.0, unintended writes
to equal zero, and injected-clock p95 latency not to exceed 100 ms. Missing usage
stays missing; token aggregates include only the one case whose fake Provider
actually returns usage. A zero denominator is `null` and fails a required gate.
Rates and averages use decimal half-up rounding to six places; p50/p95 use the
nearest-rank definition.

Recovery and duplicate behavior are intentionally absent from the fast gate.
`baseline.v2.json` records only the safe PostgreSQL evidence case IDs
`recovery-post-commit` and `recovery-competing-high-impact`; the observations are
constructed in guarded `postgres-test` integration tests from product Run, Tool
execution, task-row, and public checkpoint outcomes. No LangGraph private table is
read.

The v2 baseline is a deterministic contract benchmark. It does not measure
real-model correctness, factuality, retrieval relevance, production traffic,
network latency, availability, cost, or an SLA. Any real-Provider or human-rated
evaluation requires a separate versioned, privacy- and cost-approved run.
