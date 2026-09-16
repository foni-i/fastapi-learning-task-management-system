"""Strict serializable contracts for Stage 8 study planning."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent.grounding import CITATION_ID_PATTERN, MAX_CITATIONS_PER_CLAIM

STUDY_PLAN_PROMPT_VERSION = "study-plan.v2"


def _bounded_text(value: object, *, field_name: str, maximum: int) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} is too long")
    return normalized


class PlanningStatus(StrEnum):
    COMPLETED = "completed"


class PlanningGoal(BaseModel):
    """Accept one bounded untrusted learning goal and optional constraints."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    objective: str = Field(min_length=1, max_length=2000)
    constraints: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator("objective", mode="before")
    @classmethod
    def normalize_objective(cls, value: object) -> object:
        return _bounded_text(value, field_name="Planning objective", maximum=2000)

    @field_validator("constraints", mode="before")
    @classmethod
    def normalize_constraints(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            return value
        return tuple(
            _bounded_text(item, field_name="Planning constraint", maximum=500)
            for item in value
        )


class StudyPlanStep(BaseModel):
    """Represent one ordered, bounded, externally visible plan step."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    step_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    position: int = Field(ge=1, le=20)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    success_criteria: str = Field(min_length=1, max_length=500)
    citation_ids: tuple[
        Annotated[str, Field(pattern=CITATION_ID_PATTERN, max_length=100)], ...
    ] = Field(default=(), max_length=MAX_CITATIONS_PER_CLAIM)

    @field_validator("title", "description", "success_criteria", mode="before")
    @classmethod
    def normalize_text(cls, value: object, info: object) -> object:
        field_name = getattr(info, "field_name", "Plan text")
        maximum = {"title": 200, "description": 1000, "success_criteria": 500}[
            field_name
        ]
        return _bounded_text(value, field_name=field_name, maximum=maximum)

    @field_validator("citation_ids")
    @classmethod
    def require_unique_citations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Plan step citations must be unique")
        return tuple(sorted(value))


class StudyPlan(BaseModel):
    """Expose one safe summary and a complete deterministic sequence."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    summary: str = Field(min_length=1, max_length=1000)
    steps: tuple[StudyPlanStep, ...] = Field(min_length=1, max_length=20)

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: object) -> object:
        return _bounded_text(value, field_name="Plan summary", maximum=1000)

    @model_validator(mode="after")
    def validate_sequence(self) -> Self:
        keys = [step.step_key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("Plan step keys must be unique")
        positions = [step.position for step in self.steps]
        if positions != list(range(1, len(self.steps) + 1)):
            raise ValueError("Plan step positions must be contiguous and ordered")
        return self


class PlanningResult(BaseModel):
    """Return only a versioned validated plan and stable status."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    prompt_version: str = Field(pattern=r"^study-plan\.v[1-9][0-9]*$")
    status: PlanningStatus
    plan: StudyPlan

    @field_validator("prompt_version")
    @classmethod
    def require_current_prompt_version(cls, value: str) -> str:
        if value != STUDY_PLAN_PROMPT_VERSION:
            raise ValueError("Planning result prompt version is unsupported")
        return value
