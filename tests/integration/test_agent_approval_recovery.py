"""PostgreSQL proofs for durable approval crash and competition recovery."""

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event, Lock
from time import monotonic
from uuid import UUID, uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.agent.checkpointing import open_postgres_checkpointer
from app.agent.context import AgentRuntimeContext
from app.agent.evaluation import EvaluationDatabaseObservationV2
from app.agent.graph import AgentCheckpointStatus, AgentWorkflow, build_agent_graph
from app.agent.nodes.approval import BatchCreateTasksApprovalPreview
from app.agent.nodes.finalization import verify_result
from app.agent.providers import ProviderRequest, ProviderResponse
from app.agent.schemas import STUDY_PLAN_PROMPT_VERSION, PlanningGoal
from app.agent.state import AgentGraphInput, AgentGraphState
from app.core.exceptions import AgentRunConflictError, AgentRunNotFoundError
from app.models import AgentApproval, AgentRun, AgentThread, Project, Task, User
from app.models.agent_tool_execution import (
    AgentToolExecution,
    AgentToolExecutionStatus,
)
from app.repositories.agent_recovery import open_agent_recovery_lock
from app.schemas.agent_run import (
    AgentApprovalSubmission,
    AgentApprovalSubmissionDecision,
    AgentRunStartRequest,
)
from app.schemas.agent_tool import AgentToolMutationResult
from app.schemas.task import TaskCreate
from app.services.agent_tool_executions import AgentToolExecutionCoordinator
from app.services.agent_workflow import (
    get_agent_approval_preview,
    start_agent_run,
    submit_agent_approval,
)
from app.services.tasks import create_task_batch
from tests.integration.test_agent_interrupt_resume import (
    DurableWorkflowFactory,
    EmptyGateway,
    InterruptOnlyApproval,
    _create_user,
)

pytestmark = pytest.mark.integration


class SimulatedProcessTermination(BaseException):
    """Model process loss after the durable product decision commit."""


def _checkpoint_url(test_database_url: URL, schema_name: str) -> URL:
    return test_database_url.update_query_dict(
        {"options": f"-csearch_path={schema_name},public"}
    )


def _prepare_checkpoint_schema(
    integration_engine: Engine,
    test_database_url: URL,
    prefix: str,
) -> tuple[str, URL]:
    schema_name = f"{prefix}_{uuid4().hex}"
    checkpoint_url = _checkpoint_url(test_database_url, schema_name)
    with integration_engine.begin() as connection:
        connection.execute(CreateSchema(schema_name))
    with open_postgres_checkpointer(
        database_url=SecretStr(checkpoint_url.render_as_string(hide_password=False)),
        setup=True,
    ):
        pass
    return schema_name, checkpoint_url


def _drop_checkpoint_schema(integration_engine: Engine, schema_name: str) -> None:
    with integration_engine.begin() as connection:
        connection.execute(DropSchema(schema_name, cascade=True))


def test_postgres_public_snapshot_continuation_reaches_terminal(
    integration_engine: Engine,
    test_database_url: URL,
) -> None:
    schema_name, checkpoint_url = _prepare_checkpoint_schema(
        integration_engine,
        test_database_url,
        "agent_public_continue",
    )
    try:
        with open_postgres_checkpointer(
            database_url=SecretStr(
                checkpoint_url.render_as_string(hide_password=False)
            ),
        ) as checkpointer:
            builder = StateGraph(AgentGraphState, input_schema=AgentGraphInput)
            builder.add_node(
                "prepare",
                lambda _state: {"workflow_error_code": "SPIKE_CONTINUATION"},
            )
            builder.add_node("finish", lambda state: dict(verify_result(state)))
            builder.add_edge(START, "prepare")
            builder.add_edge("prepare", "finish")
            builder.add_edge("finish", END)
            compiled = builder.compile(checkpointer=checkpointer)
            thread_id = uuid4()
            config = {"configurable": {"thread_id": str(thread_id)}}
            compiled.invoke(  # type: ignore[call-overload]
                AgentGraphInput(goal=PlanningGoal(objective="Continue")).model_dump(
                    mode="python"
                ),
                config=config,
                interrupt_after=["prepare"],
                durability="sync",
            )
            workflow = AgentWorkflow(
                compiled,  # type: ignore[arg-type]
                checkpointing_enabled=True,
                durable_approval_enabled=True,
            )

            before = workflow.inspect_durable(thread_id=thread_id)
            completed = workflow.continue_durable(thread_id=thread_id)
            after = workflow.inspect_durable(thread_id=thread_id)

            assert before.status is AgentCheckpointStatus.CONTINUABLE
            assert completed.output is not None
            assert after.status is AgentCheckpointStatus.TERMINAL
    finally:
        _drop_checkpoint_schema(integration_engine, schema_name)


def test_postgres_recovery_lock_serializes_and_releases(
    integration_engine: Engine,
) -> None:
    factory = sessionmaker(bind=integration_engine, class_=Session)
    same_run = uuid4()
    other_run = uuid4()
    waiting = Event()
    acquired = Event()

    def acquire(run_id: UUID) -> None:
        with factory() as session:
            waiting.set()
            with open_agent_recovery_lock(session, run_id):
                acquired.set()

    pool = ThreadPoolExecutor(max_workers=2)
    try:
        with factory() as holder, open_agent_recovery_lock(holder, same_run):
            blocked = pool.submit(acquire, same_run)
            assert waiting.wait(timeout=2)
            assert not acquired.wait(timeout=0.2)

            other_acquired = Event()

            def acquire_other() -> None:
                with factory() as session, open_agent_recovery_lock(session, other_run):
                    other_acquired.set()

            independent = pool.submit(acquire_other)
            assert other_acquired.wait(timeout=2)
            independent.result(timeout=2)
            assert not blocked.done()

        assert acquired.wait(timeout=2)
        blocked.result(timeout=2)
    finally:
        pool.shutdown(wait=True)

    with (
        pytest.raises(SimulatedProcessTermination),
        factory() as session,
        open_agent_recovery_lock(session, same_run),
    ):
        raise SimulatedProcessTermination()
    with factory() as session, open_agent_recovery_lock(session, same_run):
        pass


def test_post_commit_termination_rebuild_recovers_exact_submission(
    integration_engine: Engine,
    test_database_url: URL,
) -> None:
    schema_name, checkpoint_url = _prepare_checkpoint_schema(
        integration_engine,
        test_database_url,
        "agent_commit_crash",
    )
    factory = sessionmaker(
        bind=integration_engine,
        class_=Session,
        expire_on_commit=False,
    )
    owner = _create_user(factory)
    other = _create_user(factory)
    run_id: UUID | None = None
    try:
        starter = DurableWorkflowFactory(checkpoint_url)
        with factory() as session:
            started = start_agent_run(
                AgentRunStartRequest(
                    goal=PlanningGoal(objective="Recover the committed approval")
                ),
                owner.id,
                session,
                workflow_factory=starter,
            )
        run_id = started.run.id
        assert started.approval is not None
        submission = AgentApprovalSubmission(
            revision=started.approval.revision,
            proposal_fingerprint=started.approval.proposal_fingerprint,
            decision=AgentApprovalSubmissionDecision.APPROVED,
        )

        with factory() as session, pytest.raises(SimulatedProcessTermination):
            submit_agent_approval(
                run_id,
                submission,
                owner.id,
                session,
                workflow_factory=DurableWorkflowFactory(checkpoint_url),
                after_decision_commit=lambda: (_ for _ in ()).throw(
                    SimulatedProcessTermination()
                ),
            )

        with factory() as session:
            persisted = session.get(AgentRun, run_id)
            approval = session.scalar(
                select(AgentApproval).where(AgentApproval.run_id == run_id)
            )
            assert persisted is not None and persisted.status == "RUNNING"
            assert approval is not None and approval.decision == "APPROVED"

        rebuilt = DurableWorkflowFactory(checkpoint_url)
        with factory() as session, pytest.raises(SimulatedProcessTermination):
            submit_agent_approval(
                run_id,
                submission,
                owner.id,
                session,
                workflow_factory=rebuilt,
                before_progress_commit=lambda: (_ for _ in ()).throw(
                    SimulatedProcessTermination()
                ),
            )
        assert rebuilt.open_count == 1

        with factory() as session:
            persisted = session.get(AgentRun, run_id)
            assert persisted is not None and persisted.status == "RUNNING"

        reconciler = DurableWorkflowFactory(checkpoint_url)
        with factory() as session:
            recovered = submit_agent_approval(
                run_id,
                submission,
                owner.id,
                session,
                workflow_factory=reconciler,
            )
        assert recovered.run.status.value == "SUCCEEDED"
        assert reconciler.open_count == 1

        with factory() as session:
            duplicate = submit_agent_approval(
                run_id,
                submission,
                owner.id,
                session,
                workflow_factory=DurableWorkflowFactory(checkpoint_url),
            )
        assert duplicate == recovered

        with DurableWorkflowFactory(checkpoint_url)(owner.id) as workflow:
            checkpoint_terminal = (
                workflow.inspect_durable(thread_id=started.thread.id).status
                is AgentCheckpointStatus.TERMINAL
            )
        foreign_rejected = False
        with factory() as session:
            try:
                submit_agent_approval(
                    run_id,
                    submission,
                    other.id,
                    session,
                    workflow_factory=DurableWorkflowFactory(checkpoint_url),
                )
            except AgentRunNotFoundError:
                foreign_rejected = True
        observation = EvaluationDatabaseObservationV2(
            case_id="recovery-post-commit",
            recovered=recovered.run.status.value == "SUCCEEDED",
            checkpoint_terminal=checkpoint_terminal,
            response_loss_reconciled=reconciler.open_count == 1,
            exact_replay_equal=duplicate == recovered,
            mismatched_or_foreign_rejected=foreign_rejected,
            domain_write_count=0,
            product_row_count=0,
            completed_execution_count=0,
        )
        assert observation.recovered
        assert observation.checkpoint_terminal
        assert observation.response_loss_reconciled
        assert observation.exact_replay_equal
        assert observation.mismatched_or_foreign_rejected
    finally:
        with factory.begin() as session:
            session.execute(
                delete(AgentApproval).where(
                    AgentApproval.user_id.in_((owner.id, other.id))
                )
            )
            session.execute(
                delete(AgentRun).where(AgentRun.user_id.in_((owner.id, other.id)))
            )
            session.execute(
                delete(AgentThread).where(AgentThread.user_id.in_((owner.id, other.id)))
            )
            session.execute(delete(User).where(User.id.in_((owner.id, other.id))))
        _drop_checkpoint_schema(integration_engine, schema_name)


def test_request_changes_makes_old_preview_stale_and_exposes_new_revision(
    integration_engine: Engine,
    test_database_url: URL,
) -> None:
    schema_name, checkpoint_url = _prepare_checkpoint_schema(
        integration_engine,
        test_database_url,
        "agent_preview_revision",
    )
    factory = sessionmaker(
        bind=integration_engine,
        class_=Session,
        expire_on_commit=False,
    )
    owner = _create_user(factory)
    run_id: UUID | None = None
    try:
        workflow_factory = DurableWorkflowFactory(checkpoint_url)
        with factory() as session:
            started = start_agent_run(
                AgentRunStartRequest(
                    goal=PlanningGoal(objective="Revise the persisted plan")
                ),
                owner.id,
                session,
                workflow_factory=workflow_factory,
            )
        run_id = started.run.id
        with factory() as session:
            old_preview = get_agent_approval_preview(
                run_id,
                owner.id,
                session,
                workflow_factory=workflow_factory,
            )
        change_submission = AgentApprovalSubmission(
            revision=old_preview.revision,
            proposal_fingerprint=old_preview.proposal_fingerprint,
            decision=AgentApprovalSubmissionDecision.REQUEST_CHANGES,
            feedback="Make the plan clearer",
        )
        with factory() as session:
            changed = submit_agent_approval(
                run_id,
                change_submission,
                owner.id,
                session,
                workflow_factory=workflow_factory,
            )
        assert changed.approval is not None
        assert changed.approval.revision == old_preview.revision + 1

        stale_submission = AgentApprovalSubmission(
            revision=old_preview.revision,
            proposal_fingerprint=old_preview.proposal_fingerprint,
            decision=AgentApprovalSubmissionDecision.APPROVED,
        )
        with factory() as session, pytest.raises(AgentRunConflictError):
            submit_agent_approval(
                run_id,
                stale_submission,
                owner.id,
                session,
                workflow_factory=workflow_factory,
            )
        with factory() as session:
            new_preview = get_agent_approval_preview(
                run_id,
                owner.id,
                session,
                workflow_factory=workflow_factory,
            )
        assert new_preview.revision == old_preview.revision + 1
        assert new_preview.proposal_fingerprint == changed.approval.proposal_fingerprint
    finally:
        with factory.begin() as session:
            session.execute(
                delete(AgentApproval).where(AgentApproval.user_id == owner.id)
            )
            session.execute(delete(AgentRun).where(AgentRun.user_id == owner.id))
            session.execute(delete(AgentThread).where(AgentThread.user_id == owner.id))
            session.execute(delete(User).where(User.id == owner.id))
        _drop_checkpoint_schema(integration_engine, schema_name)


class BatchProvider:
    def __init__(self, project_id: UUID) -> None:
        self._project_id = project_id

    def generate(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        del request, timeout_seconds
        tasks = [
            {"project_id": str(self._project_id), "title": f"Recovered {index}"}
            for index in (1, 2)
        ]
        return ProviderResponse(
            output_text=json.dumps(
                {
                    "planning_result": {
                        "prompt_version": STUDY_PLAN_PROMPT_VERSION,
                        "status": "completed",
                        "plan": {
                            "summary": "Create two recovered tasks",
                            "steps": [
                                {
                                    "step_key": "step_1",
                                    "position": 1,
                                    "title": "Recover",
                                    "description": "Create one safe batch",
                                    "success_criteria": "Two tasks exist",
                                }
                            ],
                        },
                    },
                    "actions": [
                        {
                            "action_key": "batch_recovery",
                            "tool_name": "batch_create_tasks",
                            "arguments": {"tasks": tasks},
                        }
                    ],
                }
            )
        )


class CountingBatchGateway(EmptyGateway):
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory
        self._counter_lock = Lock()
        self.write_count = 0

    def batch_create_tasks(
        self,
        *,
        user_id: UUID,
        task_inputs: tuple[TaskCreate, ...],
    ) -> AgentToolMutationResult:
        with self._counter_lock:
            self.write_count += 1
        with self._factory() as session:
            created = create_task_batch(task_inputs, user_id, session)
        return AgentToolMutationResult(
            operation="batch_create_tasks",
            reference_task_id=created[0].id,
            affected_count=len(created),
            summary=f"Created {len(created)} tasks",
        )


class BatchWorkflowFactory:
    def __init__(
        self,
        checkpoint_url: URL,
        project_id: UUID,
        gateway: CountingBatchGateway,
        session_factory: sessionmaker[Session],
        *,
        run_id: UUID | None = None,
    ) -> None:
        self._checkpoint_url = checkpoint_url
        self._project_id = project_id
        self._gateway = gateway
        self._session_factory = session_factory
        self._run_id = run_id

    @contextmanager
    def __call__(self, user_id: UUID) -> Iterator[AgentWorkflow]:
        with open_postgres_checkpointer(
            database_url=SecretStr(
                self._checkpoint_url.render_as_string(hide_password=False)
            )
        ) as checkpointer:
            executor = (
                None
                if self._run_id is None
                else AgentToolExecutionCoordinator(
                    run_id=self._run_id,
                    user_id=user_id,
                    gateway=self._gateway,
                    session_factory=self._session_factory,
                )
            )
            yield build_agent_graph(
                model="synthetic-test-model",
                provider=BatchProvider(self._project_id),
                gateway=self._gateway,
                runtime_context=AgentRuntimeContext(
                    user_id=user_id,
                    write_tools_enabled=True,
                ),
                approval_decider=InterruptOnlyApproval(),
                clock=monotonic,
                sleeper=lambda _: None,
                checkpointer=checkpointer,
                durable_approval=True,
                action_executor=executor,
            )


def test_competing_recovery_executes_high_impact_domain_write_once(
    integration_engine: Engine,
    test_database_url: URL,
) -> None:
    schema_name, checkpoint_url = _prepare_checkpoint_schema(
        integration_engine,
        test_database_url,
        "agent_competing_recovery",
    )
    factory = sessionmaker(
        bind=integration_engine,
        class_=Session,
        expire_on_commit=False,
    )
    owner = _create_user(factory)
    other = _create_user(factory)
    with factory.begin() as session:
        project = Project(user_id=owner.id, name="Recovery project")
        session.add(project)
        session.flush()
        project_id = project.id
    gateway = CountingBatchGateway(factory)
    run_id: UUID | None = None
    try:
        with factory() as session:
            started = start_agent_run(
                AgentRunStartRequest(
                    goal=PlanningGoal(objective="Create a recoverable batch")
                ),
                owner.id,
                session,
                workflow_factory=BatchWorkflowFactory(
                    checkpoint_url,
                    project_id,
                    gateway,
                    factory,
                ),
            )
        run_id = started.run.id
        assert started.approval is not None

        preview_factory = BatchWorkflowFactory(
            checkpoint_url,
            project_id,
            gateway,
            factory,
        )
        with factory() as session:
            preview = get_agent_approval_preview(
                run_id,
                owner.id,
                session,
                workflow_factory=preview_factory,
            )
        assert preview.revision == started.approval.revision
        assert preview.proposal_fingerprint == started.approval.proposal_fingerprint
        assert len(preview.actions) == 1
        preview_action = preview.actions[0]
        assert isinstance(preview_action, BatchCreateTasksApprovalPreview)
        assert [task.title for task in preview_action.tasks] == [
            "Recovered 1",
            "Recovered 2",
        ]
        with factory() as session, pytest.raises(AgentRunNotFoundError):
            get_agent_approval_preview(
                run_id,
                other.id,
                session,
                workflow_factory=preview_factory,
            )

        submission = AgentApprovalSubmission(
            revision=preview.revision,
            proposal_fingerprint=preview.proposal_fingerprint,
            decision=AgentApprovalSubmissionDecision.APPROVED,
        )
        workflow_factory = BatchWorkflowFactory(
            checkpoint_url,
            project_id,
            gateway,
            factory,
            run_id=run_id,
        )
        barrier = Barrier(2)

        def submit() -> object:
            barrier.wait(timeout=3)
            with factory() as session:
                return submit_agent_approval(
                    run_id,
                    submission,
                    owner.id,
                    session,
                    workflow_factory=workflow_factory,
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(submit)
            second = pool.submit(submit)
            first_result = first.result(timeout=15)
            second_result = second.result(timeout=15)

        assert first_result == second_result
        with factory() as session:
            response_lost_retry = submit_agent_approval(
                run_id,
                submission,
                owner.id,
                session,
                workflow_factory=workflow_factory,
            )
            task_count = session.scalar(
                select(func.count()).select_from(Task).where(Task.user_id == owner.id)
            )
            executions = tuple(
                session.scalars(
                    select(AgentToolExecution).where(
                        AgentToolExecution.run_id == run_id
                    )
                )
            )
            run = session.get(AgentRun, run_id)
        assert response_lost_retry == first_result
        assert gateway.write_count == 1
        assert task_count == 2
        assert len(executions) == 1
        assert executions[0].status == AgentToolExecutionStatus.COMPLETED.value
        assert run is not None and run.status == "SUCCEEDED"
        with workflow_factory(owner.id) as workflow:
            checkpoint_terminal = (
                workflow.inspect_durable(thread_id=started.thread.id).status
                is AgentCheckpointStatus.TERMINAL
            )
        observation = EvaluationDatabaseObservationV2(
            case_id="recovery-competing-high-impact",
            recovered=run is not None and run.status == "SUCCEEDED",
            checkpoint_terminal=checkpoint_terminal,
            competing_results_equal=first_result == second_result,
            response_loss_reconciled=response_lost_retry == first_result,
            exact_replay_equal=response_lost_retry == first_result,
            domain_write_count=gateway.write_count,
            product_row_count=task_count or 0,
            completed_execution_count=sum(
                execution.status == AgentToolExecutionStatus.COMPLETED.value
                for execution in executions
            ),
        )
        assert observation == EvaluationDatabaseObservationV2(
            case_id="recovery-competing-high-impact",
            recovered=True,
            checkpoint_terminal=True,
            competing_results_equal=True,
            response_loss_reconciled=True,
            exact_replay_equal=True,
            domain_write_count=1,
            product_row_count=2,
            completed_execution_count=1,
        )
    finally:
        with factory.begin() as session:
            session.execute(
                delete(AgentToolExecution).where(AgentToolExecution.user_id == owner.id)
            )
            session.execute(
                delete(AgentApproval).where(AgentApproval.user_id == owner.id)
            )
            session.execute(delete(AgentRun).where(AgentRun.user_id == owner.id))
            session.execute(delete(AgentThread).where(AgentThread.user_id == owner.id))
            session.execute(delete(Task).where(Task.user_id == owner.id))
            session.execute(delete(Project).where(Project.user_id == owner.id))
            session.execute(delete(User).where(User.id.in_((owner.id, other.id))))
        _drop_checkpoint_schema(integration_engine, schema_name)
