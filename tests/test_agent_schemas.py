"""Strict boundary tests for Stage 8 goal, plan, and result schemas."""

import pytest
from pydantic import ValidationError

from app.agent.schemas import (
    STUDY_PLAN_PROMPT_VERSION,
    PlanningGoal,
    PlanningResult,
    PlanningStatus,
    StudyPlan,
    StudyPlanStep,
)


def _step(position: int = 1, key: str = "step_1") -> StudyPlanStep:
    return StudyPlanStep(
        step_key=key,
        position=position,
        title="Read the chapter",
        description="Read the selected chapter and take concise notes.",
        success_criteria="A one-page note exists.",
    )


def _result() -> PlanningResult:
    return PlanningResult(
        prompt_version=STUDY_PLAN_PROMPT_VERSION,
        status=PlanningStatus.COMPLETED,
        plan=StudyPlan(summary="A bounded plan.", steps=(_step(),)),
    )


def test_goal_trims_bounded_text_and_serializes_to_json() -> None:
    goal = PlanningGoal(
        objective="  Learn database transactions  ",
        constraints=("  Finish this week  ",),
    )

    assert goal.objective == "Learn database transactions"
    assert goal.constraints == ("Finish this week",)
    assert PlanningGoal.model_validate_json(goal.model_dump_json()) == goal


@pytest.mark.parametrize(
    "payload",
    [
        {"objective": " "},
        {"objective": "x" * 2001},
        {"objective": "valid", "constraints": [" "]},
        {"objective": "valid", "constraints": ["x" * 501]},
        {"objective": "valid", "constraints": ["x"] * 21},
        {"objective": "valid", "unexpected": True},
    ],
)
def test_goal_rejects_invalid_bounds_and_extra_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PlanningGoal.model_validate(payload)


def test_plan_requires_unique_contiguous_ordered_steps() -> None:
    valid = StudyPlan(
        summary="Two steps.",
        steps=(_step(1, "step_1"), _step(2, "step_2")),
    )
    assert [step.position for step in valid.steps] == [1, 2]

    with pytest.raises(ValidationError, match="keys must be unique"):
        StudyPlan(
            summary="Duplicate keys.",
            steps=(_step(1, "same"), _step(2, "same")),
        )
    with pytest.raises(ValidationError, match="contiguous and ordered"):
        StudyPlan(
            summary="Missing first position.",
            steps=(_step(2, "step_2"),),
        )
    with pytest.raises(ValidationError, match="contiguous and ordered"):
        StudyPlan(
            summary="Out of order.",
            steps=(_step(2, "step_2"), _step(1, "step_1")),
        )


def test_result_has_an_explicit_public_field_allowlist() -> None:
    result = _result()
    dumped = result.model_dump(mode="json")

    assert set(dumped) == {"prompt_version", "status", "plan"}
    assert set(dumped["plan"]) == {"summary", "steps"}
    assert "password" not in result.model_dump_json()
    assert "api_key" not in result.model_dump_json()
    assert PlanningResult.model_validate_json(result.model_dump_json()) == result


def test_result_rejects_unsupported_prompt_version_and_extra_fields() -> None:
    payload = _result().model_dump(mode="json")
    payload["prompt_version"] = "study-plan.v1"

    with pytest.raises(ValidationError, match="prompt version is unsupported"):
        PlanningResult.model_validate(payload)

    payload = _result().model_dump(mode="json")
    payload["internal_reasoning"] = "must not be accepted"
    with pytest.raises(ValidationError):
        PlanningResult.model_validate(payload)


def test_step_text_boundaries_and_key_format_are_enforced() -> None:
    with pytest.raises(ValidationError):
        StudyPlanStep(
            step_key="Invalid Key",
            position=1,
            title="title",
            description="description",
            success_criteria="criteria",
        )
    with pytest.raises(ValidationError):
        StudyPlanStep(
            step_key="step_1",
            position=1,
            title=" ",
            description="description",
            success_criteria="criteria",
        )
