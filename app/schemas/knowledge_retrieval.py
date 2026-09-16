"""Strict public contracts for owner-scoped knowledge retrieval."""

from math import isfinite
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent.retrieval import MAX_RETRIEVAL_CANDIDATES

MAX_KNOWLEDGE_QUERY_CHARACTERS = 2_000
MAX_KNOWLEDGE_DOCUMENT_FILTERS = 20
MAX_KNOWLEDGE_RESULTS = 20
DEFAULT_KNOWLEDGE_TOP_K = 10
MAX_KNOWLEDGE_EXCERPT_CHARACTERS = 500


class KnowledgeSearchQuery(BaseModel):
    """Accept bounded model-controlled search terms without trusted identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    query: str = Field(min_length=1, max_length=MAX_KNOWLEDGE_QUERY_CHARACTERS)
    top_k: int = Field(default=DEFAULT_KNOWLEDGE_TOP_K, ge=1, le=MAX_KNOWLEDGE_RESULTS)
    document_ids: tuple[UUID, ...] | None = Field(
        default=None,
        max_length=MAX_KNOWLEDGE_DOCUMENT_FILTERS,
    )

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("knowledge search query must not be blank")
        return normalized


class KnowledgeCitation(BaseModel):
    """Expose one bounded source reference without private storage fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    citation_id: str = Field(min_length=1, max_length=100)
    document_id: UUID
    chunk_id: UUID
    source: str = Field(min_length=1, max_length=255)
    page_number: int | None = Field(default=None, ge=1, le=100)
    ordinal: int = Field(ge=0, le=199)
    distance: float | None = Field(default=None, ge=0, le=2)
    vector_rank: int | None = Field(default=None, ge=1, le=MAX_RETRIEVAL_CANDIDATES)
    lexical_rank: int | None = Field(default=None, ge=1, le=MAX_RETRIEVAL_CANDIDATES)
    fusion_score: float = Field(gt=0, le=1)
    excerpt: str = Field(
        min_length=1,
        max_length=MAX_KNOWLEDGE_EXCERPT_CHARACTERS,
    )

    @field_validator("distance", "fusion_score")
    @classmethod
    def require_finite_score(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("knowledge ranking score must be finite")
        return value

    @model_validator(mode="after")
    def require_consistent_rank_evidence(self) -> KnowledgeCitation:
        if self.vector_rank is None and self.lexical_rank is None:
            raise ValueError("at least one retrieval rank is required")
        if (self.vector_rank is None) != (self.distance is None):
            raise ValueError("distance must match vector rank presence")
        return self


class KnowledgeSearchResult(BaseModel):
    """Return at most the requested bounded set of public citations."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    items: tuple[KnowledgeCitation, ...] = Field(max_length=MAX_KNOWLEDGE_RESULTS)
