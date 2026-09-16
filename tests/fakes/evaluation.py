"""Deterministic, offline-only dependencies for the Stage 11 evaluation runner."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent.embeddings import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProviderUnavailableError,
)
from app.agent.evaluation import (
    DependencyOutcome,
    EvaluationCase,
    EvaluationCitationStimulus,
    EvaluationDependencyError,
    EvaluationErrorCode,
    EvaluationInputPath,
    EvaluationProviderStimulus,
    EvaluationScenarioV2,
)
from app.agent.evaluation_harness import (
    EVALUATION_CHUNK_ID,
    EVALUATION_CITATION_ID,
    EVALUATION_DOCUMENT_ID,
    EVALUATION_PROJECT_ID,
    EVALUATION_UNKNOWN_CITATION_ID,
    EvaluationDependenciesV2,
    EvaluationGatewayStimulus,
)
from app.agent.providers import (
    ProviderRequest,
    ProviderResponse,
    ProviderTransientError,
    ProviderUsage,
)
from app.models import TaskPriority, TaskStatus
from app.repositories.knowledge_document_chunks import (
    KnowledgeDocumentChunkMatch,
    KnowledgeDocumentLexicalMatch,
)
from app.schemas.knowledge_retrieval import (
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
)
from app.schemas.project import ProjectListResponse
from app.schemas.task import (
    PublicTask,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskUpdate,
)
from app.services.knowledge_retrieval import search_owned_knowledge


class ScriptedEvaluationModel:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, case: EvaluationCase) -> None:
        self.calls.append(case.case_id)
        if case.fake_script.model is DependencyOutcome.FAILS:
            raise EvaluationDependencyError(EvaluationErrorCode.MODEL_FAILED)


class ScriptedEvaluationEmbedding:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, case: EvaluationCase) -> None:
        self.calls.append(case.case_id)
        if case.fake_script.embedding is DependencyOutcome.FAILS:
            raise EvaluationDependencyError(EvaluationErrorCode.EMBEDDING_FAILED)


class ScriptedEvaluationRetrieval:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, case: EvaluationCase) -> None:
        self.calls.append(case.case_id)
        if case.fake_script.retrieval is DependencyOutcome.FAILS:
            raise EvaluationDependencyError(EvaluationErrorCode.RETRIEVAL_FAILED)


class DryRunEvaluationTool:
    """Record names and counts only; this class has no production gateway."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self._real_write_count = 0

    @property
    def real_write_count(self) -> int:
        return self._real_write_count

    def dry_run(self, case: EvaluationCase) -> int:
        self.calls.append(
            (case.fake_script.tool_profile.value, case.fake_script.dry_run_write_count)
        )
        if case.fake_script.tool is DependencyOutcome.FAILS:
            raise EvaluationDependencyError(EvaluationErrorCode.TOOL_FAILED)
        return case.fake_script.dry_run_write_count


class FixedEvaluationClock:
    def __init__(self, values: tuple[float, ...] = (100.0, 100.0)) -> None:
        self._values = values
        self._index = 0

    def __call__(self) -> float:
        value = self._values[self._index % len(self._values)]
        self._index += 1
        return value


def _citation_ids(scenario: EvaluationScenarioV2) -> list[str]:
    if scenario.citation is EvaluationCitationStimulus.UNKNOWN:
        return [EVALUATION_UNKNOWN_CITATION_ID]
    if scenario.citation is EvaluationCitationStimulus.DUPLICATE:
        return [EVALUATION_CITATION_ID, EVALUATION_CITATION_ID]
    if scenario.citation is EvaluationCitationStimulus.MISSING:
        return []
    if (
        scenario.input_path is EvaluationInputPath.GROUNDING
        or scenario.citation is EvaluationCitationStimulus.FIXTURE
    ):
        return [EVALUATION_CITATION_ID]
    return []


def _proposal_payload(scenario: EvaluationScenarioV2) -> str:
    step_count = (
        21 if scenario.provider is EvaluationProviderStimulus.TOO_MANY_STEPS else 1
    )
    steps = [
        {
            "step_key": f"step_{index}",
            "position": index,
            "title": f"Synthetic step {index}",
            "description": "Exercise the production planning schema.",
            "success_criteria": "A bounded observation exists.",
            "citation_ids": _citation_ids(scenario),
        }
        for index in range(1, step_count + 1)
    ]
    action_count = 0
    if scenario.provider is EvaluationProviderStimulus.VALID_WRITE:
        action_count = 1
    elif scenario.provider is EvaluationProviderStimulus.TOO_MANY_ACTIONS:
        action_count = 4
    actions = [
        {
            "action_key": f"create_{index}",
            "tool_name": "create_task",
            "arguments": {
                "project_id": str(EVALUATION_PROJECT_ID),
                "title": f"Synthetic task {index}",
            },
        }
        for index in range(1, action_count + 1)
    ]
    return json.dumps(
        {
            "planning_result": {
                "prompt_version": "study-plan.v2",
                "status": "completed",
                "plan": {
                    "summary": "Synthetic evaluation plan",
                    "steps": steps,
                },
            },
            "actions": actions,
        }
    )


class ProtocolEvaluationProvider:
    """Implement ModelProvider and record only actual production requests."""

    def __init__(self, scenario: EvaluationScenarioV2) -> None:
        self._scenario = scenario
        self.requests: list[ProviderRequest] = []
        self.responses: list[ProviderResponse] = []
        self.failure_count = 0

    def generate(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        self.requests.append(ProviderRequest.model_validate(request))
        if self._scenario.provider is EvaluationProviderStimulus.TRANSIENT_FAILURE:
            self.failure_count += 1
            raise ProviderTransientError("synthetic provider failure")
        output = (
            "{"
            if self._scenario.provider is EvaluationProviderStimulus.INVALID_JSON
            else _proposal_payload(self._scenario)
        )
        usage = self._scenario.usage
        response = ProviderResponse(
            output_text=output,
            usage=ProviderUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=(
                    usage.input_tokens + usage.output_tokens
                    if usage.input_tokens is not None
                    and usage.output_tokens is not None
                    else None
                ),
            ),
        )
        self.responses.append(response)
        return response


class _EvaluationEmbeddingProvider:
    def __init__(self, succeeds: bool) -> None:
        self._succeeds = succeeds
        self.call_count = 0

    def embed(
        self,
        texts: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> Sequence[Sequence[float]]:
        self.call_count += 1
        if not self._succeeds:
            raise EmbeddingProviderUnavailableError("synthetic embedding failure")
        return (tuple(0.0 for _ in range(EMBEDDING_DIMENSIONS)),)


class _EvaluationRepository:
    def __init__(self, stimulus: EvaluationGatewayStimulus) -> None:
        self._stimulus = stimulus
        self.call_count = 0

    def _fail_if_requested(self) -> None:
        self.call_count += 1
        if not self._stimulus.retrieval_succeeds:
            raise SQLAlchemyError("synthetic retrieval failure")

    def search_vector_owned(
        self,
        *,
        user_id: UUID,
        query_embedding: Sequence[float],
        top_k: int,
        document_ids: Sequence[UUID] | None,
    ) -> tuple[KnowledgeDocumentChunkMatch, ...]:
        self._fail_if_requested()
        if self._stimulus.input_path is not EvaluationInputPath.GROUNDING:
            return ()
        return (
            KnowledgeDocumentChunkMatch(
                chunk_id=EVALUATION_CHUNK_ID,
                document_id=EVALUATION_DOCUMENT_ID,
                source="synthetic-evaluation.txt",
                page_number=1,
                ordinal=0,
                content=self._stimulus.input_text,
                distance=0.0,
            ),
        )

    def search_lexical_owned(
        self,
        *,
        user_id: UUID,
        query: str,
        top_k: int,
        document_ids: Sequence[UUID] | None,
    ) -> tuple[KnowledgeDocumentLexicalMatch, ...]:
        self._fail_if_requested()
        return ()


class ServiceBackedEvaluationGateway:
    """Use the production Tool and retrieval Service boundaries without writes."""

    def __init__(self, stimulus: EvaluationGatewayStimulus) -> None:
        self._stimulus = stimulus
        self._embedding = _EvaluationEmbeddingProvider(stimulus.embedding_succeeds)
        self._repository = _EvaluationRepository(stimulus)
        self.write_count = 0
        self.search_failure_count = 0
        self.last_search_result: KnowledgeSearchResult | None = None

    @property
    def embedding_call_count(self) -> int:
        return self._embedding.call_count

    @property
    def repository_call_count(self) -> int:
        return self._repository.call_count

    def list_projects(
        self,
        *,
        user_id: UUID,
        page: int,
        page_size: int,
        include_archived: bool,
    ) -> ProjectListResponse:
        return ProjectListResponse(
            items=[], page=page, page_size=page_size, total=0, pages=0
        )

    def list_tasks(
        self,
        *,
        user_id: UUID,
        query: TaskListQuery,
    ) -> TaskListResponse:
        return TaskListResponse(
            items=[], page=query.page, page_size=query.page_size, total=0, pages=0
        )

    def search_knowledge(
        self,
        *,
        user_id: UUID,
        search_query: KnowledgeSearchQuery,
    ) -> KnowledgeSearchResult:
        try:
            result = search_owned_knowledge(
                search_query,
                user_id,
                cast(Session, object()),
                embedding_provider=self._embedding,
                timeout_seconds=1.0,
                repository_factory=lambda _session: self._repository,
            )
        except Exception:
            self.search_failure_count += 1
            raise
        self.last_search_result = result
        return result

    def create_task(
        self,
        *,
        user_id: UUID,
        task_input: TaskCreate,
    ) -> PublicTask:
        self.write_count += 1
        now = datetime(2026, 9, 14, tzinfo=UTC)
        return PublicTask(
            id=EVALUATION_CHUNK_ID,
            project_id=task_input.project_id,
            title=task_input.title,
            description=task_input.description,
            status=TaskStatus.TODO,
            priority=TaskPriority.MEDIUM,
            planned_date=None,
            due_at=None,
            estimated_minutes=None,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )

    def update_task(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        task_update: TaskUpdate,
    ) -> PublicTask:
        raise AssertionError("v2 evaluation does not propose update_task")


def evaluation_v2_dependencies() -> EvaluationDependenciesV2:
    return EvaluationDependenciesV2(
        provider_factory=ProtocolEvaluationProvider,
        gateway_factory=ServiceBackedEvaluationGateway,
    )
