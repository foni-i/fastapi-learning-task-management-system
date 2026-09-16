"""Deterministic metric, threshold, and baseline tests for Task 11.8."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.evaluation import (
    EvaluationDependencies,
    EvaluationReport,
    EvaluationReportV2,
    load_evaluation_dataset,
    load_evaluation_dataset_v2,
    run_evaluation,
)
from app.agent.evaluation_harness import run_evaluation_v2
from app.agent.evaluation_metrics import (
    EVALUATION_BASELINE_VERSION,
    EVALUATION_V2_BASELINE_VERSION,
    PREDECLARED_THRESHOLDS,
    PREDECLARED_THRESHOLDS_V2,
    EvaluationBaseline,
    EvaluationBaselineV2,
    GateMetricName,
    RateMetric,
    build_evaluation_baseline,
    build_evaluation_baseline_v2,
    calculate_evaluation_metrics,
    calculate_evaluation_metrics_v2,
    evaluate_thresholds,
    evaluate_thresholds_v2,
    evaluation_gate_exit_code,
    nearest_rank_percentile,
    normalized_evaluation_baseline,
    normalized_evaluation_baseline_v2,
    rate_metric,
)
from tests.fakes.evaluation import (
    DryRunEvaluationTool,
    FixedEvaluationClock,
    ScriptedEvaluationEmbedding,
    ScriptedEvaluationModel,
    ScriptedEvaluationRetrieval,
    evaluation_v2_dependencies,
)

DATASET_PATH = Path("evals/stage11/dataset.v1.jsonl")
BASELINE_PATH = Path("evals/stage11/baseline.v1.json")
DATASET_V2_PATH = Path("evals/stage11/dataset.v2.jsonl")
BASELINE_V2_PATH = Path("evals/stage11/baseline.v2.json")


def _report() -> EvaluationReport:
    dataset = load_evaluation_dataset(DATASET_PATH.read_text(encoding="utf-8"))
    dependencies = EvaluationDependencies(
        model=ScriptedEvaluationModel(),
        embedding=ScriptedEvaluationEmbedding(),
        retrieval=ScriptedEvaluationRetrieval(),
        tool=DryRunEvaluationTool(),
        clock=FixedEvaluationClock(),
    )
    return run_evaluation(dataset, dependencies)


def test_metric_formulas_have_explicit_deterministic_denominators() -> None:
    metrics = calculate_evaluation_metrics(_report())

    assert metrics.extraction_exact_match == RateMetric(
        numerator=3, denominator=3, value=1
    )
    assert metrics.tool_name_accuracy == RateMetric(numerator=3, denominator=3, value=1)
    assert metrics.tool_argument_accuracy == RateMetric(
        numerator=4, denominator=4, value=1
    )
    assert metrics.citation_validity_accuracy == RateMetric(
        numerator=4, denominator=4, value=1
    )
    assert metrics.plan_violation_rate == RateMetric(
        numerator=0, denominator=3, value=0
    )
    assert metrics.unintended_write_rate == RateMetric(
        numerator=0, denominator=39, value=0
    )
    assert metrics.approval_bypass_rate == RateMetric(
        numerator=0, denominator=3, value=0
    )
    assert metrics.recovery_success_rate == RateMetric(
        numerator=1, denominator=2, value=0.5
    )
    assert metrics.duplicate_write_rate == RateMetric(
        numerator=0, denominator=3, value=0
    )


def test_token_metrics_preserve_missing_values_and_use_known_denominators() -> None:
    metrics = calculate_evaluation_metrics(_report())

    assert metrics.input_tokens.model_dump() == {
        "known_count": 2,
        "total": 120,
        "average": 60.0,
    }
    assert metrics.output_tokens.model_dump() == {
        "known_count": 2,
        "total": 30,
        "average": 15.0,
    }
    assert metrics.total_tokens.model_dump() == {
        "known_count": 2,
        "total": 150,
        "average": 75.0,
    }


def test_rate_rounding_and_zero_denominator_are_explicit() -> None:
    assert rate_metric(2, 3).value == 0.666667
    assert rate_metric(0, 0) == RateMetric(numerator=0, denominator=0, value=None)
    with pytest.raises(ValidationError):
        RateMetric(numerator=1, denominator=0, value=None)


@pytest.mark.parametrize(
    ("values", "percentile", "expected"),
    [
        ((), 50, None),
        ((7.25,), 95, 7.25),
        ((1.0, 2.0, 3.0), 50, 2.0),
        ((1.0, 2.0, 3.0, 4.0), 50, 2.0),
        ((1.0, 2.0, 3.0, 4.0), 95, 4.0),
    ],
)
def test_nearest_rank_percentile(
    values: tuple[float, ...], percentile: int, expected: float | None
) -> None:
    assert nearest_rank_percentile(values, percentile) == expected


def test_predeclared_thresholds_pass_at_boundary_and_fail_below_it() -> None:
    metrics = calculate_evaluation_metrics(_report())
    gate = evaluate_thresholds(metrics)

    assert gate.passed
    assert evaluation_gate_exit_code(gate) == 0
    assert tuple(item.metric for item in gate.results) == tuple(
        item.metric for item in PREDECLARED_THRESHOLDS
    )

    below_boundary = metrics.model_copy(
        update={
            "recovery_success_rate": RateMetric(
                numerator=24,
                denominator=49,
                value=0.489796,
            )
        }
    )
    failed = evaluate_thresholds(below_boundary)
    assert not failed.passed
    assert evaluation_gate_exit_code(failed) == 1


def test_missing_required_metric_fails_closed() -> None:
    metrics = calculate_evaluation_metrics(_report()).model_copy(
        update={"recovery_success_rate": RateMetric(numerator=0, denominator=0)}
    )
    gate = evaluate_thresholds(metrics)

    recovery = next(
        item
        for item in gate.results
        if item.metric is GateMetricName.RECOVERY_SUCCESS_RATE
    )
    assert recovery.observed is None
    assert recovery.passed is False
    assert gate.passed is False


def test_baseline_is_stable_strict_safe_and_matches_committed_artifact() -> None:
    first = build_evaluation_baseline(_report())
    second = build_evaluation_baseline(_report())
    committed = EvaluationBaseline.model_validate_json(
        BASELINE_PATH.read_text(encoding="utf-8")
    )

    assert first.baseline_version == EVALUATION_BASELINE_VERSION
    assert normalized_evaluation_baseline(first) == normalized_evaluation_baseline(
        second
    )
    assert committed == first
    assert len(first.case_ids) == len(set(first.case_ids)) == 39
    serialized = normalized_evaluation_baseline(first)
    for forbidden in (
        "Prompt",
        "excerpt",
        '"arguments":',
        '"embedding":',
        "postgresql://",
        "Authorization",
        "Traceback",
        "hidden reasoning",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("baseline_version", "stage11-baseline.v2"),
        ("unexpected", "value"),
    ],
)
def test_baseline_rejects_unknown_version_and_extra_fields(
    field: str, value: object
) -> None:
    raw = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    raw[field] = value
    with pytest.raises(ValidationError):
        EvaluationBaseline.model_validate(raw)


def test_baseline_rejects_unsafe_ids_and_inconsistent_derived_values() -> None:
    raw = build_evaluation_baseline(_report()).model_dump(mode="python")
    raw["case_ids"] = ("unsafe case id", *raw["case_ids"][1:])
    with pytest.raises(ValidationError):
        EvaluationBaseline.model_validate(raw)

    raw = build_evaluation_baseline(_report()).model_dump(mode="python")
    raw["metrics"]["total_tokens"]["average"] = 0
    with pytest.raises(ValidationError):
        EvaluationBaseline.model_validate(raw)

    raw = build_evaluation_baseline(_report()).model_dump(mode="python")
    raw["gate"]["results"][0]["passed"] = False
    with pytest.raises(ValidationError):
        EvaluationBaseline.model_validate(raw)


@pytest.mark.parametrize("invalid", [-1, float("nan"), float("inf")])
def test_metric_contracts_reject_negative_or_non_finite_values(invalid: float) -> None:
    raw = build_evaluation_baseline(_report()).model_dump(mode="python")
    raw["metrics"]["latency_p95_ms"] = invalid
    with pytest.raises(ValidationError):
        EvaluationBaseline.model_validate(raw)


def _report_v2() -> EvaluationReportV2:
    dataset = load_evaluation_dataset_v2(DATASET_V2_PATH.read_text(encoding="utf-8"))
    return run_evaluation_v2(dataset, evaluation_v2_dependencies())


def test_v2_metrics_name_only_observed_production_contracts() -> None:
    metrics = calculate_evaluation_metrics_v2(_report_v2())

    assert metrics.goal_propagation_accuracy == RateMetric(
        numerator=3, denominator=3, value=1.0
    )
    assert metrics.tool_contract_accuracy == RateMetric(
        numerator=9, denominator=9, value=1.0
    )
    assert metrics.citation_contract_accuracy == RateMetric(
        numerator=4, denominator=4, value=1.0
    )
    assert metrics.plan_contract_accuracy == RateMetric(
        numerator=4, denominator=4, value=1.0
    )
    assert metrics.hostile_input_integrity == RateMetric(
        numerator=3, denominator=3, value=1.0
    )
    assert metrics.approval_boundary_accuracy == RateMetric(
        numerator=3, denominator=3, value=1.0
    )
    assert metrics.safe_error_contract_accuracy == RateMetric(
        numerator=2, denominator=2, value=1.0
    )
    assert metrics.unintended_write_count == 0
    assert metrics.input_tokens.known_count == 1
    assert metrics.total_tokens.total == 150


def test_v2_gate_is_predeclared_and_uses_no_recovery_claim_from_quick_run() -> None:
    metrics = calculate_evaluation_metrics_v2(_report_v2())
    gate = evaluate_thresholds_v2(metrics)

    assert gate.passed
    assert tuple(item.metric for item in gate.results) == tuple(
        item.metric for item in PREDECLARED_THRESHOLDS_V2
    )
    assert "recovery" not in gate.model_dump_json()
    assert "duplicate" not in gate.model_dump_json()


def test_v2_baseline_is_repeatable_safe_and_matches_versioned_artifact() -> None:
    first = build_evaluation_baseline_v2(_report_v2())
    second = build_evaluation_baseline_v2(_report_v2())
    committed = EvaluationBaselineV2.model_validate_json(
        BASELINE_V2_PATH.read_text(encoding="utf-8")
    )

    assert first.baseline_version == EVALUATION_V2_BASELINE_VERSION
    assert normalized_evaluation_baseline_v2(
        first
    ) == normalized_evaluation_baseline_v2(second)
    assert committed == first
    serialized = normalized_evaluation_baseline_v2(first)
    for forbidden in (
        "Prompt",
        "excerpt",
        '"arguments":',
        '"embedding":',
        "postgresql://",
        "Authorization",
        "Traceback",
        "hidden reasoning",
    ):
        assert forbidden not in serialized
