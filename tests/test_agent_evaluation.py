"""Offline dataset and deterministic Runner acceptance tests for Task 11.7."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.evaluation import (
    EVALUATION_DATASET_VERSION,
    EVALUATION_V2_DATASET_VERSION,
    MAX_EVALUATION_CASES,
    MIN_EVALUATION_CASES,
    EvaluationCase,
    EvaluationCaseV2,
    EvaluationCategory,
    EvaluationDependencies,
    EvaluationErrorCode,
    EvaluationExpectedObservationV2,
    EvaluationInput,
    EvaluationOutcome,
    EvaluationScenarioV2,
    LoadedEvaluationDataset,
    LoadedEvaluationDatasetV2,
    MalformedEvaluationCase,
    load_evaluation_dataset,
    load_evaluation_dataset_v2,
    normalized_evaluation_report,
    normalized_evaluation_report_v2,
    run_evaluation,
)
from app.agent.evaluation_harness import EvaluationDependenciesV2, run_evaluation_v2
from app.agent.evaluation_metrics import calculate_evaluation_metrics_v2
from app.agent.providers import ProviderRequest
from tests.fakes.evaluation import (
    DryRunEvaluationTool,
    FixedEvaluationClock,
    ProtocolEvaluationProvider,
    ScriptedEvaluationEmbedding,
    ScriptedEvaluationModel,
    ScriptedEvaluationRetrieval,
    ServiceBackedEvaluationGateway,
    evaluation_v2_dependencies,
)

DATASET_PATH = Path("evals/stage11/dataset.v1.jsonl")
DATASET_V2_PATH = Path("evals/stage11/dataset.v2.jsonl")


def _dependencies() -> tuple[EvaluationDependencies, DryRunEvaluationTool]:
    tool = DryRunEvaluationTool()
    return (
        EvaluationDependencies(
            model=ScriptedEvaluationModel(),
            embedding=ScriptedEvaluationEmbedding(),
            retrieval=ScriptedEvaluationRetrieval(),
            tool=tool,
            clock=FixedEvaluationClock(),
        ),
        tool,
    )


def _dataset_text() -> str:
    return DATASET_PATH.read_text(encoding="utf-8")


def test_committed_dataset_is_strict_versioned_unique_and_complete() -> None:
    dataset = load_evaluation_dataset(_dataset_text())
    cases = [entry for entry in dataset.entries if isinstance(entry, EvaluationCase)]

    assert MIN_EVALUATION_CASES <= len(cases) <= MAX_EVALUATION_CASES
    assert len(cases) == len(dataset.entries)
    assert {case.dataset_version for case in cases} == {EVALUATION_DATASET_VERSION}
    assert len({case.case_id for case in cases}) == len(cases)
    assert {case.category for case in cases} == set(EvaluationCategory)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_version", "stage11-eval.v2"),
        ("case_id", " "),
        ("category", "subjective_judge"),
    ],
)
def test_case_schema_rejects_unknown_or_unbounded_contract_values(
    field: str, value: object
) -> None:
    raw = json.loads(_dataset_text().splitlines()[0])
    raw[field] = value
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(raw)


def test_case_schema_rejects_extra_fields_long_text_and_external_values() -> None:
    raw = json.loads(_dataset_text().splitlines()[0])
    raw["metadata"] = {}
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(raw)

    raw.pop("metadata")
    raw["input"]["text"] = "x" * 2_001
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(raw)

    raw["input"]["text"] = "postgresql://synthetic.invalid/private"
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(raw)


def test_loader_marks_malformed_and_duplicate_rows_with_safe_ids_only() -> None:
    lines = _dataset_text().splitlines()[:30]
    duplicate = json.loads(lines[1])
    duplicate["case_id"] = json.loads(lines[0])["case_id"]
    lines[1] = json.dumps(duplicate)
    hostile_raw = '{"case_id":"safe-malformed","secret":"DO_NOT_ECHO"'
    lines[2] = hostile_raw

    dataset = load_evaluation_dataset("\n".join(lines))

    assert isinstance(dataset.entries[1], MalformedEvaluationCase)
    assert dataset.entries[1].case_id == "extraction-goal-basic"
    assert isinstance(dataset.entries[2], MalformedEvaluationCase)
    assert dataset.entries[2].case_id == "line-0003"
    assert "DO_NOT_ECHO" not in repr(dataset)


def test_runner_is_repeatable_ordered_fake_only_and_write_free() -> None:
    dataset = load_evaluation_dataset(_dataset_text())
    first_dependencies, first_tool = _dependencies()
    second_dependencies, second_tool = _dependencies()

    first = run_evaluation(dataset, first_dependencies)
    second = run_evaluation(dataset, second_dependencies)

    assert normalized_evaluation_report(first) == normalized_evaluation_report(second)
    assert [item.case_id for item in first.cases] == [
        entry.case_id for entry in dataset.entries
    ]
    assert first.aggregate.total_cases == 39
    assert first.aggregate.succeeded == 39
    assert first.aggregate.failed == 0
    assert first.aggregate.malformed == 0
    assert first.aggregate.unintended_real_writes == 0
    assert first_tool.real_write_count == second_tool.real_write_count == 0


def test_runner_detects_an_unintended_write_counter_without_real_side_effects() -> None:
    class DetectionRecorder(DryRunEvaluationTool):
        def dry_run(self, case: EvaluationCase) -> int:
            count = super().dry_run(case)
            if case.case_id == "writes-dry-run-only":
                self._real_write_count += 1
            return count

    dataset = load_evaluation_dataset(_dataset_text())
    tool = DetectionRecorder()
    dependencies = EvaluationDependencies(
        model=ScriptedEvaluationModel(),
        embedding=ScriptedEvaluationEmbedding(),
        retrieval=ScriptedEvaluationRetrieval(),
        tool=tool,
        clock=FixedEvaluationClock(),
    )

    report = run_evaluation(dataset, dependencies)

    assert report.aggregate.unintended_real_writes == 1


def test_runner_reports_expected_safe_evidence_without_payloads() -> None:
    dataset = load_evaluation_dataset(_dataset_text())
    dependencies, _ = _dependencies()
    report = run_evaluation(dataset, dependencies)
    by_id = {item.case_id: item for item in report.cases}

    assert by_id["tool-name-unknown"].tool_name_valid is False
    assert by_id["tool-args-identity"].tool_arguments_valid is False
    assert by_id["citation-unknown"].citation_valid is False
    assert by_id["approval-hostile-bypass-blocked"].approval_respected is True
    assert by_id["error-model-safe"].error_code is EvaluationErrorCode.MODEL_FAILED
    assert by_id["recovery-retry-success"].recovery_outcome == "recovered"
    assert by_id["duplicate-second-deduplicated"].duplicate_outcome == "deduplicated"
    assert by_id["usage-known"].total_tokens == 150
    assert by_id["usage-known"].latency_ms == 25.5

    serialized = normalized_evaluation_report(report)
    for forbidden in (
        "SYNTHETIC HOSTILE FIXTURE",
        "hidden reasoning",
        "Synthetic task",
        "project_id",
        "vector",
        "postgresql://",
        "Traceback",
    ):
        assert forbidden not in serialized


def test_runner_continues_after_malformed_case_and_report_round_trips() -> None:
    lines = _dataset_text().splitlines()
    lines[4] = '{"case_id":"safe-bad","payload":"PRIVATE_FIXTURE"'
    dataset = load_evaluation_dataset("\n".join(lines))
    dependencies, _ = _dependencies()

    report = run_evaluation(dataset, dependencies)
    restored = type(report).model_validate_json(report.model_dump_json())

    assert restored == report
    assert report.aggregate.total_cases == 39
    assert report.aggregate.malformed == 1
    assert report.aggregate.failed == 1
    malformed = report.cases[4]
    assert malformed.case_id == "line-0005"
    assert malformed.category is None
    assert malformed.outcome is EvaluationOutcome.FAILED
    assert malformed.error_code is EvaluationErrorCode.CASE_INVALID
    assert "PRIVATE_FIXTURE" not in report.model_dump_json()


def test_report_and_case_evidence_have_exact_public_fields() -> None:
    dataset = load_evaluation_dataset(_dataset_text())
    dependencies, _ = _dependencies()
    report = run_evaluation(dataset, dependencies)

    assert set(report.model_dump()) == {
        "report_version",
        "dataset_version",
        "cases",
        "aggregate",
    }
    assert set(report.cases[0].model_dump()) == {
        "dataset_version",
        "case_id",
        "category",
        "outcome",
        "error_code",
        "tool_name_valid",
        "tool_arguments_valid",
        "citation_valid",
        "approval_respected",
        "dry_run_write_count",
        "recovery_outcome",
        "duplicate_outcome",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_ms",
    }


def _v2_dataset() -> LoadedEvaluationDatasetV2:
    return load_evaluation_dataset_v2(DATASET_V2_PATH.read_text(encoding="utf-8"))


def test_v1_mutation_reproduction_proves_historical_fixture_self_attestation() -> None:
    dataset = load_evaluation_dataset(_dataset_text())
    entries = list(dataset.entries)
    index = next(
        index
        for index, entry in enumerate(entries)
        if entry.case_id == "hostile-role-markers"
    )
    case = entries[index]
    assert isinstance(case, EvaluationCase)
    entries[index] = case.model_copy(
        update={
            "input": EvaluationInput(
                text="Benign synthetic study note",
                synthetic_fixture=True,
            )
        }
    )

    original = run_evaluation(dataset, _dependencies()[0]).cases[index]
    mutated = run_evaluation(
        LoadedEvaluationDataset(entries=tuple(entries)), _dependencies()[0]
    ).cases[index]

    assert original == mutated


def test_v2_dataset_removes_self_attesting_script_fields() -> None:
    dataset = _v2_dataset()
    cases = [entry for entry in dataset.entries if isinstance(entry, EvaluationCaseV2)]

    assert len(cases) == len(dataset.entries) == 30
    assert {case.dataset_version for case in cases} == {EVALUATION_V2_DATASET_VERSION}
    assert len({case.case_id for case in cases}) == 30
    raw = DATASET_V2_PATH.read_text(encoding="utf-8")
    for forbidden in (
        '"fake_script"',
        '"approval_profile"',
        '"recovery_outcome"',
        '"duplicate_outcome"',
        '"dry_run_write_count"',
    ):
        assert forbidden not in raw


def test_v2_runner_is_deterministic_and_uses_production_provider_requests() -> None:
    providers: list[ProtocolEvaluationProvider] = []

    def provider_factory(scenario: EvaluationScenarioV2) -> ProtocolEvaluationProvider:
        provider = ProtocolEvaluationProvider(scenario)
        providers.append(provider)
        return provider

    dependencies = EvaluationDependenciesV2(
        provider_factory=provider_factory,
        gateway_factory=ServiceBackedEvaluationGateway,
    )
    first = run_evaluation_v2(_v2_dataset(), dependencies)
    second = run_evaluation_v2(_v2_dataset(), evaluation_v2_dependencies())

    assert first.aggregate.total_cases == first.aggregate.succeeded == 30
    assert first.aggregate.failed == first.aggregate.malformed == 0
    assert first.aggregate.unintended_writes == 0
    assert normalized_evaluation_report_v2(first) == normalized_evaluation_report_v2(
        second
    )
    assert all(
        isinstance(request, ProviderRequest)
        for provider in providers
        for request in provider.requests
    )
    assert sum(bool(provider.requests) for provider in providers) == 29


def test_v2_expectation_changes_only_judgement_not_observation() -> None:
    dataset = _v2_dataset()
    entries = list(dataset.entries)
    case = entries[0]
    assert isinstance(case, EvaluationCaseV2)
    assert case.expectation.goal_character_count is not None
    entries[0] = case.model_copy(
        update={
            "expectation": EvaluationExpectedObservationV2(
                goal_character_count=case.expectation.goal_character_count + 1
            )
        }
    )

    original = run_evaluation_v2(dataset, evaluation_v2_dependencies()).cases[0]
    changed = run_evaluation_v2(
        LoadedEvaluationDatasetV2(entries=tuple(entries)),
        evaluation_v2_dependencies(),
    ).cases[0]

    assert original.observation == changed.observation
    assert original.outcome is EvaluationOutcome.SUCCEEDED
    assert changed.outcome is EvaluationOutcome.FAILED


def test_v2_hostile_input_mutation_changes_grounding_observation_and_gate_input() -> (
    None
):
    dataset = _v2_dataset()
    entries = list(dataset.entries)
    index = next(
        index
        for index, entry in enumerate(entries)
        if entry.case_id == "hostile-delimiter"
    )
    case = entries[index]
    assert isinstance(case, EvaluationCaseV2)
    entries[index] = case.model_copy(
        update={
            "input": EvaluationInput(
                text="Benign bounded document evidence",
                synthetic_fixture=True,
            )
        }
    )

    original_report = run_evaluation_v2(dataset, evaluation_v2_dependencies())
    mutated_report = run_evaluation_v2(
        LoadedEvaluationDatasetV2(entries=tuple(entries)),
        evaluation_v2_dependencies(),
    )
    original = original_report.cases[index]
    mutated = mutated_report.cases[index]

    assert original.observation is not None
    assert mutated.observation is not None
    assert original.observation.grounding_delimiter_escaped is True
    assert mutated.observation.grounding_delimiter_escaped is False
    assert original.outcome is EvaluationOutcome.SUCCEEDED
    assert mutated.outcome is EvaluationOutcome.FAILED
    original_metric = calculate_evaluation_metrics_v2(
        original_report
    ).hostile_input_integrity
    mutated_metric = calculate_evaluation_metrics_v2(
        mutated_report
    ).hostile_input_integrity
    assert (original_metric.numerator, original_metric.denominator) == (3, 3)
    assert (mutated_metric.numerator, mutated_metric.denominator) == (2, 3)


def test_v2_observations_come_from_validators_interrupts_usage_and_clock() -> None:
    report = run_evaluation_v2(_v2_dataset(), evaluation_v2_dependencies())
    by_id = {item.case_id: item.observation for item in report.cases}

    observed = tuple(by_id.values())
    assert all(item is not None for item in observed)
    assert by_id["tool-name-unknown"] is not None
    assert by_id["tool-name-unknown"].tool_name_valid is False
    assert by_id["tool-args-identity"] is not None
    assert by_id["tool-args-identity"].tool_arguments_valid is False
    assert by_id["citation-unknown"] is not None
    assert by_id["citation-unknown"].citation_valid is False
    assert by_id["approval-pauses"] is not None
    assert by_id["approval-pauses"].approval_respected is True
    assert by_id["approval-pauses"].write_count == 0
    assert by_id["approval-approved"] is not None
    assert by_id["approval-approved"].write_count == 1
    assert by_id["error-embedding"] is not None
    assert by_id["error-embedding"].provider_called is False
    usage = by_id["usage-provider"]
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (
        120,
        30,
        150,
    )
    assert usage.latency_ms == 25.5
    missing = by_id["goal-basic"]
    assert missing is not None
    assert missing.input_tokens is missing.output_tokens is missing.total_tokens is None


def test_v2_report_is_bounded_and_excludes_complete_inputs_and_payloads() -> None:
    report = run_evaluation_v2(_v2_dataset(), evaluation_v2_dependencies())
    serialized = normalized_evaluation_report_v2(report)

    for forbidden in (
        "Build a bounded study plan",
        "STMS_UNTRUSTED_KNOWLEDGE_END",
        "Synthetic task",
        '"tool_arguments":',
        '"output_text"',
        "postgresql://",
        "Authorization",
        "Traceback",
    ):
        assert forbidden not in serialized
