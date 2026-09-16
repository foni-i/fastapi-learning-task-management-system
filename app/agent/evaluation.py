"""Versioned, deterministic, offline-only Agent evaluation contracts."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.agent.context import AgentRuntimeContext
from app.agent.grounding import (
    GroundedKnowledgeContext,
    GroundingEvidence,
    validate_citation_references,
)
from app.agent.tools import (
    AgentToolInputError,
    AgentToolNotAllowedError,
    validate_tool_arguments,
)

EVALUATION_DATASET_VERSION: Literal["stage11-eval.v1"] = "stage11-eval.v1"
EVALUATION_REPORT_VERSION: Literal["stage11-report.v1"] = "stage11-report.v1"
MIN_EVALUATION_CASES = 30
MAX_EVALUATION_CASES = 50
_SAFE_CASE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PROHIBITED_TEXT = re.compile(
    r"(?:https?://|postgres(?:ql)?://|authorization\s*:|bearer\s+[a-z0-9]|"
    r"api[_ -]?key\s*[=:]|password\s*[=:])",
    re.IGNORECASE,
)


class EvaluationCategory(StrEnum):
    EXTRACTION = "extraction"
    TOOL_NAME = "tool_name"
    TOOL_ARGUMENTS = "tool_arguments"
    CITATIONS = "citations"
    PLAN_BOUNDS = "plan_bounds"
    HOSTILE_DOCUMENT = "hostile_document"
    APPROVAL_BYPASS = "approval_bypass"
    SAFE_ERRORS = "safe_errors"
    RECOVERY = "recovery"
    DUPLICATES = "duplicates"
    UNINTENDED_WRITES = "unintended_writes"
    LATENCY_TOKENS = "latency_tokens"


class EvaluationErrorCode(StrEnum):
    NONE = "NONE"
    CASE_INVALID = "CASE_INVALID"
    MODEL_FAILED = "MODEL_FAILED"
    EMBEDDING_FAILED = "EMBEDDING_FAILED"
    RETRIEVAL_FAILED = "RETRIEVAL_FAILED"
    TOOL_FAILED = "TOOL_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"


class EvaluationOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RecoveryOutcome(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    RECOVERED = "recovered"
    EXHAUSTED = "exhausted"


class DuplicateOutcome(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    DEDUPLICATED = "deduplicated"
    REPEATED = "repeated"


class ToolProfile(StrEnum):
    NONE = "none"
    READ_VALID = "read_valid"
    WRITE_VALID = "write_valid"
    UNKNOWN = "unknown"
    FORBIDDEN_IDENTITY = "forbidden_identity"
    FORBIDDEN_SESSION = "forbidden_session"
    FORBIDDEN_SQL = "forbidden_sql"
    FORBIDDEN_VECTOR = "forbidden_vector"


class CitationProfile(StrEnum):
    NONE = "none"
    VALID = "valid"
    UNKNOWN = "unknown"
    DUPLICATE = "duplicate"
    TOO_MANY = "too_many"
    MISSING_WITH_EVIDENCE = "missing_with_evidence"
    FABRICATED_WITHOUT_EVIDENCE = "fabricated_without_evidence"


class PlanProfile(StrEnum):
    VALID = "valid"
    TOO_MANY_STEPS = "too_many_steps"
    TOO_MANY_ACTIONS = "too_many_actions"
    DUPLICATE_ACTION = "duplicate_action"


class ApprovalProfile(StrEnum):
    RESPECTED = "respected"
    BYPASS_BLOCKED = "bypass_blocked"
    BYPASS_ALLOWED = "bypass_allowed"


class DependencyOutcome(StrEnum):
    SUCCEEDS = "succeeds"
    FAILS = "fails"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class EvaluationInput(_StrictModel):
    text: str = Field(min_length=1, max_length=2_000)
    synthetic_fixture: Literal[True]

    @field_validator("text")
    @classmethod
    def reject_sensitive_or_external_values(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or _PROHIBITED_TEXT.search(normalized):
            raise ValueError("evaluation text is unsafe")
        return normalized


class EvaluationFakeScript(_StrictModel):
    model: DependencyOutcome = DependencyOutcome.SUCCEEDS
    embedding: DependencyOutcome = DependencyOutcome.SUCCEEDS
    retrieval: DependencyOutcome = DependencyOutcome.SUCCEEDS
    tool: DependencyOutcome = DependencyOutcome.SUCCEEDS
    tool_profile: ToolProfile = ToolProfile.NONE
    citation_profile: CitationProfile = CitationProfile.NONE
    plan_profile: PlanProfile = PlanProfile.VALID
    approval_profile: ApprovalProfile = ApprovalProfile.RESPECTED
    recovery_outcome: RecoveryOutcome = RecoveryOutcome.NOT_APPLICABLE
    duplicate_outcome: DuplicateOutcome = DuplicateOutcome.NOT_APPLICABLE
    dry_run_write_count: int = Field(default=0, ge=0, le=3)
    input_tokens: int | None = Field(default=None, ge=0, le=100_000)
    output_tokens: int | None = Field(default=None, ge=0, le=100_000)
    latency_ms: float = Field(default=0, ge=0, le=60_000)


class EvaluationExpectation(_StrictModel):
    error_code: EvaluationErrorCode = EvaluationErrorCode.NONE
    tool_name_valid: bool
    tool_arguments_valid: bool
    citation_valid: bool
    approval_respected: bool
    recovery_outcome: RecoveryOutcome = RecoveryOutcome.NOT_APPLICABLE
    duplicate_outcome: DuplicateOutcome = DuplicateOutcome.NOT_APPLICABLE
    dry_run_write_count: int = Field(default=0, ge=0, le=3)


class EvaluationCase(_StrictModel):
    dataset_version: Literal["stage11-eval.v1"]
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    category: EvaluationCategory
    input: EvaluationInput
    fake_script: EvaluationFakeScript
    expectation: EvaluationExpectation


class MalformedEvaluationCase(_StrictModel):
    case_id: str = Field(pattern=r"^(?:[a-z][a-z0-9_-]{0,63}|line-[0-9]{4})$")


class LoadedEvaluationDataset(_StrictModel):
    entries: tuple[EvaluationCase | MalformedEvaluationCase, ...] = Field(
        min_length=MIN_EVALUATION_CASES, max_length=MAX_EVALUATION_CASES
    )


class EvaluationCaseEvidence(_StrictModel):
    dataset_version: Literal["stage11-eval.v1"]
    case_id: str = Field(pattern=r"^(?:[a-z][a-z0-9_-]{0,63}|line-[0-9]{4})$")
    category: EvaluationCategory | None
    outcome: EvaluationOutcome
    error_code: EvaluationErrorCode
    tool_name_valid: bool
    tool_arguments_valid: bool
    citation_valid: bool
    approval_respected: bool
    dry_run_write_count: int = Field(ge=0, le=3)
    recovery_outcome: RecoveryOutcome
    duplicate_outcome: DuplicateOutcome
    input_tokens: int | None = Field(default=None, ge=0, le=100_000)
    output_tokens: int | None = Field(default=None, ge=0, le=100_000)
    total_tokens: int | None = Field(default=None, ge=0, le=200_000)
    latency_ms: float = Field(ge=0, le=60_000)


class EvaluationCategoryCount(_StrictModel):
    category: EvaluationCategory
    count: int = Field(ge=0, le=MAX_EVALUATION_CASES)


class EvaluationAggregate(_StrictModel):
    total_cases: int = Field(ge=MIN_EVALUATION_CASES, le=MAX_EVALUATION_CASES)
    category_counts: tuple[EvaluationCategoryCount, ...] = Field(
        min_length=len(EvaluationCategory), max_length=len(EvaluationCategory)
    )
    succeeded: int = Field(ge=0, le=MAX_EVALUATION_CASES)
    failed: int = Field(ge=0, le=MAX_EVALUATION_CASES)
    malformed: int = Field(ge=0, le=MAX_EVALUATION_CASES)
    unintended_real_writes: int = Field(ge=0, le=MAX_EVALUATION_CASES)

    @model_validator(mode="after")
    def require_consistent_counts(self) -> Self:
        if self.succeeded + self.failed != self.total_cases:
            raise ValueError("evaluation aggregate counts are inconsistent")
        categories = tuple(item.category for item in self.category_counts)
        if categories != tuple(EvaluationCategory):
            raise ValueError("evaluation category counts are incomplete or unordered")
        if (
            sum(item.count for item in self.category_counts) + self.malformed
            != self.total_cases
        ):
            raise ValueError("evaluation category totals are inconsistent")
        return self


class EvaluationReport(_StrictModel):
    report_version: Literal["stage11-report.v1"] = EVALUATION_REPORT_VERSION
    dataset_version: Literal["stage11-eval.v1"] = EVALUATION_DATASET_VERSION
    cases: tuple[EvaluationCaseEvidence, ...] = Field(
        min_length=MIN_EVALUATION_CASES, max_length=MAX_EVALUATION_CASES
    )
    aggregate: EvaluationAggregate


class EvaluationDependencyError(RuntimeError):
    def __init__(self, code: EvaluationErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class EvaluationModel(Protocol):
    def run(self, case: EvaluationCase) -> None: ...


class EvaluationEmbedding(Protocol):
    def run(self, case: EvaluationCase) -> None: ...


class EvaluationRetrieval(Protocol):
    def run(self, case: EvaluationCase) -> None: ...


class EvaluationToolRecorder(Protocol):
    @property
    def real_write_count(self) -> int: ...

    def dry_run(self, case: EvaluationCase) -> int: ...


@dataclass(frozen=True, slots=True)
class EvaluationDependencies:
    model: EvaluationModel
    embedding: EvaluationEmbedding
    retrieval: EvaluationRetrieval
    tool: EvaluationToolRecorder
    clock: Callable[[], float]


class DryRunToolRecorder:
    """Record bounded safe classifications without any production write path."""

    def __init__(self) -> None:
        self.calls: list[tuple[ToolProfile, int]] = []

    @property
    def real_write_count(self) -> int:
        return 0

    def dry_run(self, case: EvaluationCase) -> int:
        self.calls.append(
            (case.fake_script.tool_profile, case.fake_script.dry_run_write_count)
        )
        return case.fake_script.dry_run_write_count


def _safe_case_id(value: object, line_number: int) -> str:
    if isinstance(value, str) and _SAFE_CASE_ID.fullmatch(value):
        return value
    return f"line-{line_number:04d}"


def load_evaluation_dataset(text: str) -> LoadedEvaluationDataset:
    """Parse JSONL independently and retain only a safe ID for invalid rows."""

    lines = text.splitlines()
    entries: list[EvaluationCase | MalformedEvaluationCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        raw: object = None
        try:
            raw = json.loads(line)
            case = EvaluationCase.model_validate(raw)
            if case.case_id in seen:
                raise ValueError("duplicate case ID")
            seen.add(case.case_id)
            entries.append(case)
        except json.JSONDecodeError, TypeError, ValueError, ValidationError:
            candidate = raw.get("case_id") if isinstance(raw, dict) else None
            entries.append(
                MalformedEvaluationCase(case_id=_safe_case_id(candidate, line_number))
            )
    return LoadedEvaluationDataset(entries=tuple(entries))


_FIXTURE_USER_ID = UUID("00000000-0000-4000-8000-000000000001")
_FIXTURE_PROJECT_ID = "00000000-0000-4000-8000-000000000002"
_FIXTURE_DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000003")
_FIXTURE_CHUNK_ID = UUID("00000000-0000-4000-8000-000000000004")
_FIXTURE_CITATION_ID = f"knowledge:{_FIXTURE_DOCUMENT_ID}:{_FIXTURE_CHUNK_ID}"


def _tool_profile_evidence(profile: ToolProfile) -> tuple[bool, bool]:
    if profile is ToolProfile.NONE:
        return True, True
    name = "list_projects"
    arguments: dict[str, object] = {}
    if profile is ToolProfile.READ_VALID:
        arguments = {"page": 1, "page_size": 20}
    elif profile is ToolProfile.WRITE_VALID:
        name = "create_task"
        arguments = {"project_id": _FIXTURE_PROJECT_ID, "title": "Synthetic task"}
    elif profile is ToolProfile.UNKNOWN:
        name = "synthetic_unknown"
    elif profile is ToolProfile.FORBIDDEN_IDENTITY:
        arguments = {"user_id": str(_FIXTURE_USER_ID)}
    elif profile is ToolProfile.FORBIDDEN_SESSION:
        arguments = {"session": "synthetic"}
    elif profile is ToolProfile.FORBIDDEN_SQL:
        name = "search_knowledge"
        arguments = {"query": "synthetic", "sql": "synthetic-control"}
    elif profile is ToolProfile.FORBIDDEN_VECTOR:
        name = "search_knowledge"
        arguments = {"query": "synthetic", "vector": [0.0]}
    try:
        validate_tool_arguments(
            name,
            arguments,
            AgentRuntimeContext(user_id=_FIXTURE_USER_ID, write_tools_enabled=True),
        )
    except AgentToolNotAllowedError:
        return False, False
    except AgentToolInputError:
        return True, False
    return True, True


def _citation_profile_valid(profile: CitationProfile) -> bool:
    if profile is CitationProfile.NONE:
        return True
    evidence = GroundingEvidence(
        citation_id=_FIXTURE_CITATION_ID,
        document_id=_FIXTURE_DOCUMENT_ID,
        chunk_id=_FIXTURE_CHUNK_ID,
        source="synthetic.txt",
        ordinal=0,
        excerpt="Synthetic evidence.",
        vector_rank=1,
        fusion_score=0.1,
    )
    context = GroundedKnowledgeContext(evidence=(evidence,))
    references: tuple[str, ...]
    if profile is CitationProfile.VALID:
        references = (_FIXTURE_CITATION_ID,)
    elif profile is CitationProfile.UNKNOWN:
        references = (
            "knowledge:00000000-0000-4000-8000-000000000005:"
            "00000000-0000-4000-8000-000000000006",
        )
    elif profile is CitationProfile.DUPLICATE:
        references = (_FIXTURE_CITATION_ID, _FIXTURE_CITATION_ID)
    elif profile is CitationProfile.TOO_MANY:
        references = tuple(_FIXTURE_CITATION_ID for _ in range(11))
    elif profile is CitationProfile.MISSING_WITH_EVIDENCE:
        references = ()
    else:
        context = GroundedKnowledgeContext()
        references = (_FIXTURE_CITATION_ID,)
    try:
        validate_citation_references((references,), context)
    except ValueError:
        return False
    return True


def _profile_evidence(case: EvaluationCase) -> tuple[bool, bool, bool, bool]:
    tool_name_valid, tool_arguments_valid = _tool_profile_evidence(
        case.fake_script.tool_profile
    )
    citation_valid = _citation_profile_valid(case.fake_script.citation_profile)
    approval_respected = (
        case.fake_script.approval_profile is not ApprovalProfile.BYPASS_ALLOWED
    )
    return tool_name_valid, tool_arguments_valid, citation_valid, approval_respected


def _observed_error(
    case: EvaluationCase, dependencies: EvaluationDependencies
) -> tuple[EvaluationErrorCode, int]:
    if case.fake_script.plan_profile is not PlanProfile.VALID:
        return EvaluationErrorCode.VALIDATION_FAILED, 0
    try:
        dependencies.model.run(case)
        dependencies.embedding.run(case)
        dependencies.retrieval.run(case)
        dry_run_write_count = dependencies.tool.dry_run(case)
    except EvaluationDependencyError as exc:
        return exc.code, 0
    except Exception:
        return EvaluationErrorCode.VALIDATION_FAILED, 0
    if not 0 <= dry_run_write_count <= 3:
        return EvaluationErrorCode.VALIDATION_FAILED, 0
    return EvaluationErrorCode.NONE, dry_run_write_count


def _run_case(
    case: EvaluationCase, dependencies: EvaluationDependencies
) -> tuple[EvaluationCaseEvidence, int]:
    real_writes_before = dependencies.tool.real_write_count
    started = dependencies.clock()
    error_code, dry_run_write_count = _observed_error(case, dependencies)
    finished = dependencies.clock()
    tool_name_valid, tool_arguments_valid, citation_valid, approval_respected = (
        _profile_evidence(case)
    )
    actual = (
        error_code,
        tool_name_valid,
        tool_arguments_valid,
        citation_valid,
        approval_respected,
        case.fake_script.recovery_outcome,
        case.fake_script.duplicate_outcome,
        dry_run_write_count,
    )
    expected = (
        case.expectation.error_code,
        case.expectation.tool_name_valid,
        case.expectation.tool_arguments_valid,
        case.expectation.citation_valid,
        case.expectation.approval_respected,
        case.expectation.recovery_outcome,
        case.expectation.duplicate_outcome,
        case.expectation.dry_run_write_count,
    )
    input_tokens = case.fake_script.input_tokens
    output_tokens = case.fake_script.output_tokens
    total_tokens = (
        input_tokens + output_tokens
        if input_tokens is not None and output_tokens is not None
        else None
    )
    latency_ms = case.fake_script.latency_ms + max(0.0, (finished - started) * 1000)
    evidence = EvaluationCaseEvidence(
        dataset_version=case.dataset_version,
        case_id=case.case_id,
        category=case.category,
        outcome=EvaluationOutcome.SUCCEEDED
        if actual == expected
        else EvaluationOutcome.FAILED,
        error_code=error_code,
        tool_name_valid=tool_name_valid,
        tool_arguments_valid=tool_arguments_valid,
        citation_valid=citation_valid,
        approval_respected=approval_respected,
        dry_run_write_count=dry_run_write_count,
        recovery_outcome=case.fake_script.recovery_outcome,
        duplicate_outcome=case.fake_script.duplicate_outcome,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
    )
    real_write_delta = max(
        0,
        dependencies.tool.real_write_count - real_writes_before,
    )
    return evidence, real_write_delta


def run_evaluation(
    dataset: LoadedEvaluationDataset, dependencies: EvaluationDependencies
) -> EvaluationReport:
    """Continue after invalid cases and emit only bounded allowlisted evidence."""

    cases: list[EvaluationCaseEvidence] = []
    unintended_writes = 0
    for entry in dataset.entries:
        if isinstance(entry, MalformedEvaluationCase):
            cases.append(
                EvaluationCaseEvidence(
                    dataset_version=EVALUATION_DATASET_VERSION,
                    case_id=entry.case_id,
                    category=None,
                    outcome=EvaluationOutcome.FAILED,
                    error_code=EvaluationErrorCode.CASE_INVALID,
                    tool_name_valid=False,
                    tool_arguments_valid=False,
                    citation_valid=False,
                    approval_respected=False,
                    dry_run_write_count=0,
                    recovery_outcome=RecoveryOutcome.NOT_APPLICABLE,
                    duplicate_outcome=DuplicateOutcome.NOT_APPLICABLE,
                    latency_ms=0,
                )
            )
            continue
        evidence, real_writes = _run_case(entry, dependencies)
        cases.append(evidence)
        unintended_writes += real_writes

    counts = Counter(item.category for item in cases if item.category is not None)
    succeeded = sum(item.outcome is EvaluationOutcome.SUCCEEDED for item in cases)
    malformed = sum(
        item.error_code is EvaluationErrorCode.CASE_INVALID for item in cases
    )
    aggregate = EvaluationAggregate(
        total_cases=len(cases),
        category_counts=tuple(
            EvaluationCategoryCount(category=category, count=counts[category])
            for category in EvaluationCategory
        ),
        succeeded=succeeded,
        failed=len(cases) - succeeded,
        malformed=malformed,
        unintended_real_writes=unintended_writes,
    )
    return EvaluationReport(cases=tuple(cases), aggregate=aggregate)


def normalized_evaluation_report(report: EvaluationReport) -> str:
    """Return a canonical representation suitable for repeat-run comparison."""

    return json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


# The v1 contracts above remain loadable as historical evidence.  R6 deliberately
# introduces new names instead of pretending that the fixture-driven v1 evidence
# had the same semantics.
EVALUATION_V2_DATASET_VERSION: Literal["stage11-eval.v2"] = "stage11-eval.v2"
EVALUATION_V2_REPORT_VERSION: Literal["stage11-report.v2"] = "stage11-report.v2"


class EvaluationInputPath(StrEnum):
    GOAL = "goal"
    GROUNDING = "grounding"


class EvaluationProviderStimulus(StrEnum):
    VALID = "valid"
    VALID_WRITE = "valid_write"
    TOO_MANY_STEPS = "too_many_steps"
    TOO_MANY_ACTIONS = "too_many_actions"
    INVALID_JSON = "invalid_json"
    TRANSIENT_FAILURE = "transient_failure"


class EvaluationApprovalStimulus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PAUSE_ONLY = "pause_only"
    APPROVE = "approve"
    REJECT = "reject"


class EvaluationCitationStimulus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    FIXTURE = "fixture"
    UNKNOWN = "unknown"
    DUPLICATE = "duplicate"
    MISSING = "missing"


class EvaluationUsageStimulus(_StrictModel):
    input_tokens: int | None = Field(default=None, ge=0, le=100_000)
    output_tokens: int | None = Field(default=None, ge=0, le=100_000)


class EvaluationScenarioV2(_StrictModel):
    """Describe dependency stimuli without encoding final observations."""

    input_path: EvaluationInputPath = EvaluationInputPath.GOAL
    provider: EvaluationProviderStimulus = EvaluationProviderStimulus.VALID
    embedding: DependencyOutcome = DependencyOutcome.SUCCEEDS
    retrieval: DependencyOutcome = DependencyOutcome.SUCCEEDS
    tool_name: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
    )
    tool_arguments: dict[str, object] | None = None
    citation: EvaluationCitationStimulus = EvaluationCitationStimulus.NOT_APPLICABLE
    approval: EvaluationApprovalStimulus = EvaluationApprovalStimulus.NOT_APPLICABLE
    usage: EvaluationUsageStimulus = Field(default_factory=EvaluationUsageStimulus)
    clock_seconds: tuple[float, float] = (1.0, 1.0)

    @model_validator(mode="after")
    def require_bounded_scenario_pairs(self) -> Self:
        if (self.tool_name is None) != (self.tool_arguments is None):
            raise ValueError("Tool stimulus name and arguments must be paired")
        start, finish = self.clock_seconds
        if start < 0 or finish < start or finish - start > 60:
            raise ValueError("Evaluation clock stimulus is invalid")
        if (
            self.approval is not EvaluationApprovalStimulus.NOT_APPLICABLE
            and self.provider is not EvaluationProviderStimulus.VALID_WRITE
        ):
            raise ValueError("Approval stimulus requires a write proposal")
        return self


class EvaluationExpectedObservationV2(_StrictModel):
    """Hold independent gold values used only after observation is complete."""

    error_code: EvaluationErrorCode = EvaluationErrorCode.NONE
    goal_propagated: bool = True
    goal_character_count: int | None = Field(default=None, ge=1, le=2_000)
    grounding_propagated: bool | None = None
    grounding_delimiter_escaped: bool | None = None
    provider_called: bool = True
    plan_valid: bool | None = True
    tool_name_valid: bool | None = None
    tool_arguments_valid: bool | None = None
    citation_valid: bool | None = None
    approval_respected: bool | None = None
    write_count: int = Field(default=0, ge=0, le=3)
    input_tokens: int | None = Field(default=None, ge=0, le=100_000)
    output_tokens: int | None = Field(default=None, ge=0, le=100_000)
    total_tokens: int | None = Field(default=None, ge=0, le=200_000)
    latency_ms: float | None = Field(default=None, ge=0, le=60_000)


class EvaluationCaseV2(_StrictModel):
    dataset_version: Literal["stage11-eval.v2"]
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    category: EvaluationCategory
    input: EvaluationInput
    scenario: EvaluationScenarioV2
    expectation: EvaluationExpectedObservationV2


class MalformedEvaluationCaseV2(_StrictModel):
    case_id: str = Field(pattern=r"^(?:[a-z][a-z0-9_-]{0,63}|line-[0-9]{4})$")


class LoadedEvaluationDatasetV2(_StrictModel):
    entries: tuple[EvaluationCaseV2 | MalformedEvaluationCaseV2, ...] = Field(
        min_length=MIN_EVALUATION_CASES,
        max_length=MAX_EVALUATION_CASES,
    )


class EvaluationObservationV2(_StrictModel):
    """Expose bounded facts derived from production calls, never raw payloads."""

    error_code: EvaluationErrorCode
    goal_propagated: bool
    goal_character_count: int = Field(ge=1, le=2_000)
    grounding_propagated: bool | None = None
    grounding_delimiter_escaped: bool | None = None
    provider_called: bool
    prompt_version: str | None = Field(default=None, max_length=100)
    plan_valid: bool | None = None
    tool_name_valid: bool | None = None
    tool_arguments_valid: bool | None = None
    citation_valid: bool | None = None
    approval_respected: bool | None = None
    write_count: int = Field(ge=0, le=3)
    input_tokens: int | None = Field(default=None, ge=0, le=100_000)
    output_tokens: int | None = Field(default=None, ge=0, le=100_000)
    total_tokens: int | None = Field(default=None, ge=0, le=200_000)
    latency_ms: float | None = Field(default=None, ge=0, le=60_000)


class EvaluationCaseEvidenceV2(_StrictModel):
    dataset_version: Literal["stage11-eval.v2"] = EVALUATION_V2_DATASET_VERSION
    case_id: str = Field(pattern=r"^(?:[a-z][a-z0-9_-]{0,63}|line-[0-9]{4})$")
    category: EvaluationCategory | None
    outcome: EvaluationOutcome
    observation: EvaluationObservationV2 | None
    error_code: EvaluationErrorCode


class EvaluationAggregateV2(_StrictModel):
    total_cases: int = Field(ge=MIN_EVALUATION_CASES, le=MAX_EVALUATION_CASES)
    succeeded: int = Field(ge=0, le=MAX_EVALUATION_CASES)
    failed: int = Field(ge=0, le=MAX_EVALUATION_CASES)
    malformed: int = Field(ge=0, le=MAX_EVALUATION_CASES)
    unintended_writes: int = Field(ge=0, le=MAX_EVALUATION_CASES)


class EvaluationReportV2(_StrictModel):
    report_version: Literal["stage11-report.v2"] = EVALUATION_V2_REPORT_VERSION
    dataset_version: Literal["stage11-eval.v2"] = EVALUATION_V2_DATASET_VERSION
    cases: tuple[EvaluationCaseEvidenceV2, ...] = Field(
        min_length=MIN_EVALUATION_CASES,
        max_length=MAX_EVALUATION_CASES,
    )
    aggregate: EvaluationAggregateV2


class EvaluationDatabaseObservationV2(_StrictModel):
    """Project PostgreSQL recovery evidence without product or checkpoint payloads."""

    case_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    recovered: bool
    checkpoint_terminal: bool
    competing_results_equal: bool | None = None
    response_loss_reconciled: bool | None = None
    exact_replay_equal: bool | None = None
    mismatched_or_foreign_rejected: bool | None = None
    domain_write_count: int = Field(ge=0, le=3)
    product_row_count: int = Field(ge=0, le=20)
    completed_execution_count: int = Field(ge=0, le=3)


def load_evaluation_dataset_v2(text: str) -> LoadedEvaluationDatasetV2:
    """Load v2 JSONL without retaining malformed rows or validation details."""

    entries: list[EvaluationCaseV2 | MalformedEvaluationCaseV2] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        raw: object = None
        try:
            raw = json.loads(line)
            case = EvaluationCaseV2.model_validate(raw)
            if case.case_id in seen:
                raise ValueError("duplicate case ID")
            seen.add(case.case_id)
            entries.append(case)
        except json.JSONDecodeError, TypeError, ValueError, ValidationError:
            candidate = raw.get("case_id") if isinstance(raw, dict) else None
            entries.append(
                MalformedEvaluationCaseV2(case_id=_safe_case_id(candidate, line_number))
            )
    return LoadedEvaluationDatasetV2(entries=tuple(entries))


def normalized_evaluation_report_v2(report: EvaluationReportV2) -> str:
    return json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
