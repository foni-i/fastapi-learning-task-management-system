"""Offline tests for deterministic analysis and capability-safe context nodes."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.agent.context import AgentRuntimeContext
from app.agent.nodes.context import (
    AGENT_CONTEXT_UNAVAILABLE_MESSAGE,
    CONTEXT_PAGE,
    CONTEXT_PAGE_SIZE,
    KNOWLEDGE_TOP_K,
    AgentContextUnavailableError,
    analyze_goal,
    load_context,
)
from app.agent.schemas import PlanningGoal
from app.agent.state import (
    AgentContextKind,
    AgentContextSnapshot,
    AgentGoalAnalysis,
    AgentGraphState,
)
from app.agent.tools import AgentToolGateway, AgentToolResult
from app.schemas.knowledge_retrieval import KnowledgeCitation, KnowledgeSearchResult
from app.schemas.project import ProjectListResponse
from app.schemas.task import TaskListResponse


class RecordingExecutor:
    def __init__(self, knowledge: KnowledgeSearchResult | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object], AgentRuntimeContext]] = []
        self.knowledge = knowledge or KnowledgeSearchResult(items=())

    def __call__(
        self,
        name: str,
        arguments: dict[str, object],
        context: AgentRuntimeContext,
        *,
        gateway: AgentToolGateway | None = None,
    ) -> AgentToolResult:
        assert gateway is None
        self.calls.append((name, arguments, context))
        if name == "list_projects":
            return ProjectListResponse(
                items=[],
                page=CONTEXT_PAGE,
                page_size=CONTEXT_PAGE_SIZE,
                total=0,
                pages=0,
            )
        if name == "list_tasks":
            return TaskListResponse(
                items=[],
                page=CONTEXT_PAGE,
                page_size=CONTEXT_PAGE_SIZE,
                total=0,
                pages=0,
            )
        if name == "search_knowledge":
            return self.knowledge
        raise AssertionError("context node requested a write or unknown tool")


def _analyzed_state() -> AgentGraphState:
    state = AgentGraphState(
        goal=PlanningGoal(
            objective="  Learn transaction boundaries  ",
            constraints=("  Use current projects  ",),
        )
    )
    return state.model_copy(update=analyze_goal(state))


def test_analyze_goal_is_deterministic_and_updates_only_analysis() -> None:
    state = _analyzed_state()
    first = analyze_goal(state)
    second = analyze_goal(state)

    assert first == second
    assert set(first) == {"analysis"}
    analysis = first["analysis"]
    assert isinstance(analysis, AgentGoalAnalysis)
    assert analysis.objective == "Learn transaction boundaries"
    assert analysis.constraints == ("Use current projects",)
    assert analysis.required_context == (
        AgentContextKind.PROJECTS,
        AgentContextKind.TASKS,
        AgentContextKind.KNOWLEDGE,
    )
    assert "reasoning" not in analysis.model_dump_json()


def test_load_context_uses_exact_read_tools_bounds_and_trusted_identity() -> None:
    user_id = uuid4()
    state = _analyzed_state()
    original = state.model_dump_json()
    executor = RecordingExecutor()

    update = load_context(
        state,
        runtime_context=AgentRuntimeContext(
            user_id=user_id,
            write_tools_enabled=True,
        ),
        executor=executor,
    )

    assert set(update) == {"context"}
    snapshot = update["context"]
    assert isinstance(snapshot, AgentContextSnapshot)
    assert snapshot.projects.items == []
    assert snapshot.tasks.items == []
    assert [call[0] for call in executor.calls] == [
        "list_projects",
        "list_tasks",
        "search_knowledge",
    ]
    assert executor.calls[0][1] == {
        "page": 1,
        "page_size": 20,
        "include_archived": False,
    }
    assert executor.calls[1][1] == {"page": 1, "page_size": 20}
    assert executor.calls[2][1] == {
        "query": "Learn transaction boundaries",
        "top_k": KNOWLEDGE_TOP_K,
    }
    assert all(call[2].user_id == user_id for call in executor.calls)
    assert all(not call[2].write_tools_enabled for call in executor.calls)
    assert state.model_dump_json() == original
    assert (
        AgentContextSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    )


def test_missing_or_model_selected_context_fails_before_tool_use() -> None:
    executor = RecordingExecutor()
    state = AgentGraphState(goal=PlanningGoal(objective="Learn"))

    with pytest.raises(
        AgentContextUnavailableError,
        match=AGENT_CONTEXT_UNAVAILABLE_MESSAGE,
    ):
        load_context(
            state,
            runtime_context=AgentRuntimeContext(user_id=uuid4()),
            executor=executor,
        )
    assert executor.calls == []

    selected = state.model_copy(
        update={
            "analysis": AgentGoalAnalysis(
                objective="Learn",
                required_context=(AgentContextKind.PROJECTS,),
            )
        }
    )
    with pytest.raises(AgentContextUnavailableError):
        load_context(
            selected,
            runtime_context=AgentRuntimeContext(user_id=uuid4()),
            executor=executor,
        )
    assert executor.calls == []


def test_tool_failure_is_fixed_and_does_not_echo_payload_or_identity() -> None:
    state = _analyzed_state()
    user_id = uuid4()
    diagnostic = "private lower-layer diagnostic"

    def failing_executor(
        name: str,
        arguments: dict[str, object],
        context: AgentRuntimeContext,
        *,
        gateway: AgentToolGateway | None = None,
    ) -> AgentToolResult:
        raise RuntimeError(diagnostic)

    with pytest.raises(
        AgentContextUnavailableError,
        match=AGENT_CONTEXT_UNAVAILABLE_MESSAGE,
    ) as exc_info:
        load_context(
            state,
            runtime_context=AgentRuntimeContext(user_id=user_id),
            executor=failing_executor,
        )

    message = str(exc_info.value)
    assert diagnostic not in message
    assert str(user_id) not in message
    assert "page_size" not in message


def test_context_node_source_has_no_direct_persistence_or_write_access() -> None:
    source = Path("app/agent/nodes/context.py").read_text(encoding="utf-8")
    lowered = source.lower()

    for forbidden in (
        "sqlalchemy",
        "app.repositories",
        "app.db",
        "agentdomaingateway",
        '"create_task"',
        '"update_task"',
        ".commit(",
    ):
        assert forbidden not in lowered


def test_runtime_identity_never_enters_serialized_context_update() -> None:
    user_id: UUID = uuid4()
    update = load_context(
        _analyzed_state(),
        runtime_context=AgentRuntimeContext(user_id=user_id),
        executor=RecordingExecutor(),
    )
    snapshot = update["context"]
    assert isinstance(snapshot, AgentContextSnapshot)
    serialized = snapshot.model_dump_json()

    assert str(user_id) not in serialized
    assert "user_id" not in serialized


def test_load_context_stores_only_bounded_public_search_evidence() -> None:
    document_id, chunk_id = uuid4(), uuid4()
    citation = KnowledgeCitation(
        citation_id=f"knowledge:{document_id}:{chunk_id}",
        document_id=document_id,
        chunk_id=chunk_id,
        source="notes.txt",
        page_number=None,
        ordinal=0,
        distance=None,
        vector_rank=None,
        lexical_rank=1,
        fusion_score=1 / 61,
        excerpt="hostile text remains data",
    )
    update = load_context(
        _analyzed_state(),
        runtime_context=AgentRuntimeContext(user_id=uuid4()),
        executor=RecordingExecutor(KnowledgeSearchResult(items=(citation,))),
    )

    snapshot = update["context"]
    assert isinstance(snapshot, AgentContextSnapshot)
    assert snapshot.knowledge.evidence[0].citation_id == citation.citation_id
    serialized = snapshot.model_dump_json()
    for forbidden in ("distance", "embedding", "search_vector", "sql", "user_id"):
        assert forbidden not in serialized.lower()
