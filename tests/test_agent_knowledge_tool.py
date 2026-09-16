"""Offline contract tests for the read-only search_knowledge Agent Tool."""

from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.agent.context import AgentRuntimeContext
from app.agent.tools import (
    AGENT_TOOL_INPUT_MESSAGE,
    READ_TOOL_DEFINITIONS,
    AgentToolGateway,
    AgentToolInputError,
    SearchKnowledgeToolArguments,
    execute_tool,
)
from app.schemas.knowledge_retrieval import KnowledgeSearchQuery, KnowledgeSearchResult
from app.services.agent_domain import AgentDomainGateway
from tests.fakes.embedding_provider import DeterministicEmbeddingProvider


class SearchGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, KnowledgeSearchQuery]] = []

    def search_knowledge(
        self,
        *,
        user_id: UUID,
        search_query: KnowledgeSearchQuery,
    ) -> KnowledgeSearchResult:
        self.calls.append((user_id, search_query))
        return KnowledgeSearchResult(items=())


def test_search_tool_definition_and_arguments_are_exact_and_unprivileged() -> None:
    definition = next(
        item for item in READ_TOOL_DEFINITIONS if item.name == "search_knowledge"
    )
    properties = definition.input_schema["properties"]

    assert isinstance(properties, dict)
    assert set(properties) == {"query", "top_k", "document_ids"}
    serialized = definition.model_dump_json().lower()
    for forbidden in (
        "user_id",
        "session",
        "sql",
        "tsquery",
        "embedding",
        "vector",
        "operator",
        "weight",
        "rrf",
        "rank_constant",
        "formula",
        "reranker",
    ):
        assert forbidden not in serialized
    assert set(SearchKnowledgeToolArguments.model_fields) == {
        "query",
        "top_k",
        "document_ids",
    }


@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "session",
        "sql",
        "tsquery",
        "embedding",
        "vector",
        "operator",
        "lexical_weight",
        "vector_weight",
        "rrf_constant",
        "formula",
        "reranker",
    ],
)
def test_search_tool_rejects_privileged_fields_with_safe_error(field: str) -> None:
    with pytest.raises(AgentToolInputError, match=AGENT_TOOL_INPUT_MESSAGE) as exc_info:
        execute_tool(
            "search_knowledge",
            {"query": "safe", field: "private payload"},
            AgentRuntimeContext(user_id=uuid4()),
            gateway=cast(AgentToolGateway, SearchGateway()),
        )

    assert "private payload" not in str(exc_info.value)


def test_search_tool_forwards_only_context_identity_and_validated_query() -> None:
    user_id, document_id = uuid4(), uuid4()
    gateway = SearchGateway()

    result = execute_tool(
        "search_knowledge",
        {"query": "  study plan  ", "top_k": 3, "document_ids": [str(document_id)]},
        AgentRuntimeContext(user_id=user_id),
        gateway=cast(AgentToolGateway, gateway),
    )

    assert result == KnowledgeSearchResult(items=())
    assert gateway.calls == [
        (
            user_id,
            KnowledgeSearchQuery(
                query="study plan", top_k=3, document_ids=(document_id,)
            ),
        )
    ]


def test_gateway_closes_search_session_without_commit_or_rollback() -> None:
    session = MagicMock(spec=Session)
    provider = DeterministicEmbeddingProvider()
    calls: list[tuple[KnowledgeSearchQuery, UUID, Session, object, float]] = []

    def search_service(
        query: KnowledgeSearchQuery,
        user_id: UUID,
        received_session: Session,
        *,
        embedding_provider: object,
        timeout_seconds: float,
    ) -> KnowledgeSearchResult:
        calls.append(
            (query, user_id, received_session, embedding_provider, timeout_seconds)
        )
        return KnowledgeSearchResult(items=())

    gateway = AgentDomainGateway(
        session_factory=lambda: session,
        search_knowledge_service=search_service,
        embedding_provider_factory=lambda: provider,
        embedding_timeout_seconds=9,
    )
    query = KnowledgeSearchQuery(query="knowledge")
    result = gateway.search_knowledge(user_id=uuid4(), search_query=query)

    assert result == KnowledgeSearchResult(items=())
    assert calls[0][0] is query
    assert calls[0][2] is session
    assert calls[0][3:] == (provider, 9)
    session.close.assert_called_once_with()
    session.commit.assert_not_called()
    session.flush.assert_not_called()
    session.rollback.assert_not_called()


def test_gateway_closes_session_when_search_fails() -> None:
    session = MagicMock(spec=Session)

    def failing_service(*args: object, **kwargs: object) -> KnowledgeSearchResult:
        raise RuntimeError("controlled safe failure")

    gateway = AgentDomainGateway(
        session_factory=lambda: session,
        search_knowledge_service=failing_service,
        embedding_provider_factory=DeterministicEmbeddingProvider,
        embedding_timeout_seconds=3,
    )
    with pytest.raises(RuntimeError, match="controlled safe failure"):
        gateway.search_knowledge(
            user_id=uuid4(),
            search_query=KnowledgeSearchQuery(query="knowledge"),
        )

    session.close.assert_called_once_with()
    session.commit.assert_not_called()
    session.flush.assert_not_called()
    session.rollback.assert_not_called()
