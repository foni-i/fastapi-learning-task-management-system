# Checkpoint Remediation R5 — Durable approval recovery

## 状态

- Planned
- 风险级别：高风险架构整改，必须独立实施和验收
- 类型：Stage 11～Task 12.6 candidate checkpoint remediation
- 对应审查 Finding：P1 — 审批数据库提交后、LangGraph resume 前的崩溃窗口使 run
  永久停留 `RUNNING`
- 前置依赖：R4 完成并经 owner 验收
- 后续依赖：R6 必须使用本任务最终恢复协议进行 evaluation

## 根因

`submit_agent_approval` 当前先校验 `PENDING_APPROVAL + PENDING decision + revision/
fingerprint`，随后把 approval 更新为最终决定、run 更新为 `RUNNING` 并 commit；commit
之后才打开 durable workflow 并调用 `resume_durable(Command(resume=...))`。

如果进程在第一次 commit 后、调用 resume 前终止，数据库已经消费审批，但 checkpoint
仍停在 interrupt。重试不再满足 `PENDING_APPROVAL/PENDING`，只能得到 409。若 resume
已经推进 checkpoint、但产品 run 最终状态尚未 commit，同样存在数据库与 checkpoint
不同步窗口。当前 catch 只能处理进程仍存活时捕获到的异常，并可能在无法证明 checkpoint
结果时把 run 标为 `FAILED`。

现有重启测试会在正常 interrupt 后重建 runtime，再完成一次正常 resume；它没有在第一
次产品 commit 后注入进程终止。现有工具协调器通过
`(run_id, revision, proposal_fingerprint, action_key)` 唯一约束提供高影响写的 replay/
UNKNOWN 防线，但审批恢复服务尚未利用该能力进行 checkpoint reconciliation。

## 目标

把“相同 owner、run、revision、fingerprint、decision/feedback”的重复审批请求变成一个
可恢复、可竞争且幂等的协议。恢复方先独占同一 run 的 resume 权，再检查官方 LangGraph
checkpoint：仍在匹配 interrupt 时继续 resume；已终止时只同步产品状态；处于可继续的
中间 checkpoint 时按锁定 LangGraph 版本的公开 API 继续。任何重试都不得重复执行高影响
工具，数据库产品状态最终与 checkpoint 一致。

## 当前代码基线

- `agent_runs` 已有 `PENDING`、`RUNNING`、`PENDING_APPROVAL` 和四种终态。
- `agent_approvals` 以 `(run_id, revision)` 唯一，保存 fingerprint、decision、feedback、
  decided_at。
- `AgentWorkflow` 封装 `start_durable`、`resume_durable`，内部在 invoke 后调用
  `compiled.get_state(config)`，但没有公开只读 checkpoint inspection/reconciliation API。
- graph 使用稳定 owner-scoped `thread_id` 作为 LangGraph configurable identity。
- `AgentToolExecutionCoordinator` 在 domain write 前持久化 intent；完成 replay 不调用 Tool，
  IN_PROGRESS/UNKNOWN fail closed，高影响 Tool 再验证 persisted approval。
- application Alembic head 为 `e3b7c2d9a410`；官方 checkpoint tables 不属于 Alembic。
- 锁定依赖为 LangGraph 1.2.x 与 `langgraph-checkpoint-postgres` 3.1.x；实现前必须核对 lock
  中精确版本的公开同步 continuation 行为。

## 方案比较

### 方案 A：durable transition/outbox

新增 application outbox/transition 表，在审批产品事务中同时写入“待 resume”事件，再由
幂等 consumer 获取事件、检查 checkpoint、执行 resume 并确认事件。优点是产品事务内
可靠记录工作意图，未来可支持异步 worker 和运营重试。缺点是：

- 需要新 migration、事件状态、claim/lease、失败重试和清理策略；
- 若没有常驻 worker，仍需 HTTP 重试或启动扫描器，outbox 本身不会恢复；
- 引入 worker/queue 生命周期超出当前模块化单体和 1～2 小时整改边界；
- consumer 仍必须做 checkpoint inspection 与 Tool 幂等，不能消除双存储协调问题；
- 当前审批记录已经耐久保存决定，新增事件会复制 revision/fingerprint/decision 数据。

### 方案 B：`APPROVED/RUNNING + checkpoint` 幂等恢复协议

复用现有 approval 作为 durable resume intent，复用 run 状态表示待协调，使用一个
PostgreSQL run-scoped advisory transaction lock 串行化恢复请求。获得锁后重新读取产品
记录并检查 checkpoint，按 checkpoint 事实选择 resume、continue、reconcile 或返回已完成
snapshot。优点是没有重复持久化模型或 worker，重试入口就是现有审批 endpoint，并直接
利用工具幂等 final defense。缺点是请求期间占用一个额外 PostgreSQL connection，且必须
准确验证锁定 LangGraph 版本的 checkpoint continuation 语义。

### 选择

选择方案 B。当前系统没有后台 worker/queue，approval 本身已经是持久化且唯一的意图记录，
Tool execution 表已经处理 replay。方案 B 是解决已证明崩溃窗口的最小可靠扩展。方案 A
保留为未来需要异步 resume、运营队列或跨进程调度时的演进方向；当前引入只会增加另一个
需要恢复的状态机。

本任务预计不需要 application migration。若实现证明没有新增持久字段就无法客观区分
必要状态，必须停止并重新审查；任何 migration 只能属于 R5，不能修改历史 revision。

## 范围

1. 为 AgentWorkflow 增加严格、只读、无敏感内容的 durable checkpoint inspection 结果：
   pending interrupt、continuable、terminal 或 inconsistent，并携带恢复所需的 revision/
   fingerprint 或安全终态投影。
2. 在提交审批/恢复前获取 run-scoped PostgreSQL advisory transaction lock。锁由独立、
   短生命周期 connection/transaction 持有，覆盖产品 re-read、checkpoint inspection、
   resume/reconcile 和最终产品 commit；进程终止时由 PostgreSQL 自动释放。
3. lock key 从完整 run UUID 用固定、测试过的映射生成；hash collision 只允许产生额外串行，
   不能允许并发 resume。不得使用会随 Python 进程随机化的 `hash()`。
4. 获锁后重新读取 run/thread/approval，不信任获锁前 ORM 对象。
5. 首次有效审批仍先持久化 decision 和 `RUNNING`；在该 commit 后提供精确故障注入 seam，
   供测试模拟进程终止。
6. 对已决定 approval，仅当 owner、revision、fingerprint、decision 和规范化 feedback 全部
   与重试一致时允许恢复；不同值、旧 revision/fingerprint 或 foreign owner 继续拒绝。
7. checkpoint 仍在相同 interrupt：用数据库保存的决定 resume，而不是重新信任 request。
8. checkpoint 已 terminal：不再次 invoke，只把 product run 投影到 checkpoint 终态并返回
   snapshot。
9. checkpoint 已离开 interrupt 但仍可继续：只使用锁定版本公开支持的 continuation API；
   若官方 API 无法安全证明，fail closed 并停止实施，不猜测 `invoke(None)` 语义。
10. 产品 run 已 terminal 且提交内容与相同 approval 一致：重复请求返回现有 snapshot，
    不再统一 409；不同请求仍返回冲突。
11. resume/inspection 出现未知结果时保持 `RUNNING` 可恢复，不把未证明的结果永久标为
    `FAILED`；使用固定安全 503/冲突契约，不泄露 checkpoint 细节。
12. 所有实际 write Tool 继续经过 `AgentToolExecutionCoordinator`；恢复不得提供绕过路径。

## 非目标

- 不实现后台 worker、消息队列、Redis、Celery 或定时扫描器。
- 不查询或修改官方 checkpoint 私有表；只用 LangGraph 公共 API。
- 不改变 proposal preview P0、公共 approval payload 或后续产品阶段。
- 不弱化 owner/revision/fingerprint、approval、高影响 policy 或 idempotency。
- 不自动重试 `UNKNOWN` Tool outcome，不提供 exactly-once 宣称。
- 不允许不同 decision/feedback 覆盖已提交审批。
- 不在单个 product transaction 中假装原子提交 SQLAlchemy 和 LangGraph 两套存储。

## 预计修改文件

- `app/services/agent_workflow.py`
- `app/agent/graph.py`
- `app/repositories/agent_runs.py`
- `app/core/exceptions.py`
- `app/api/v1/endpoints/agent_runs.py`
- `app/schemas/agent_run.py`（仅当需要一个安全 retry/recovery HTTP 状态投影）
- `tests/test_agent_run_service.py`
- `tests/test_agent_graph.py`
- `tests/integration/test_agent_interrupt_resume.py`
- `tests/integration/test_agent_recovery.py`
- `tests/integration/test_agent_idempotency.py`
- `docs/architecture.md`
- `docs/security-and-limitations.md`
- `README.md`
- `docs/roadmap.md`（R5 完成后记录验收）

候选新增文件：

- `app/repositories/agent_recovery.py`：只有当 advisory lock 生命周期无法清晰留在现有
  Repository/Service 时才创建。
- `tests/integration/test_agent_approval_recovery.py`：若在现有 recovery 文件中加入完整
  矩阵会降低可读性则创建。
- `alembic/versions/<generated>_add_agent_resume_transition_state.py`：当前不预计；仅在证明
  需要新持久字段并由 owner 重新接受方案后使用。

## 数据流或状态转换

```text
PENDING_APPROVAL + PENDING approval
  -> acquire run recovery lock
  -> validate exact owner/revision/fingerprint/decision
  -> persist decided approval + RUNNING
  -> commit                         [fault point A]
  -> inspect checkpoint
       matching interrupt -> Command(resume=stored decision)
       continuable state   -> official continuation operation
       terminal state      -> no invoke; reconcile product state
       inconsistent        -> fail closed, retain recoverable evidence
  -> existing Tool coordinator claim/replay/UNKNOWN rules
  -> persist next PENDING_APPROVAL or terminal run
  -> commit                         [fault point B: response may be lost]
  -> release lock -> return snapshot
```

重试矩阵：

| Product state | Checkpoint state | 相同提交 | 动作 |
| --- | --- | --- | --- |
| PENDING_APPROVAL/PENDING | matching interrupt | 是 | 首次记录决定并 resume |
| RUNNING/decided | matching interrupt | 是 | 恢复 resume |
| RUNNING/decided | continuable | 是 | 官方 continuation，不重放审批 |
| RUNNING/decided | terminal | 是 | 只 reconcile 产品状态 |
| terminal/decided | terminal | 是 | 返回既有 snapshot |
| 任意 | revision/fingerprint/decision 不同 | 否 | 固定冲突，零 resume |
| 任意 owned | checkpoint 不一致/缺失 | 是 | fail closed，零 Tool 绕过 |

## 实施步骤

1. 针对锁定 LangGraph 精确版本，用公共同步 API 写一个最小 PostgreSQL spike/test，确认
   interrupt、已 resume 中间 checkpoint、terminal snapshot 的可观察字段和 continuation
   调用；不把 spike 代码提交为生产私有表访问。
2. 先添加当前崩溃窗口的失败测试：第一次 DB commit 后触发不可捕获式故障 seam，重建
   service/workflow 对象，再提交完全相同请求。
3. 定义严格 checkpoint inspection DTO 和 AgentWorkflow facade；任何值来自已验证
   `AgentGraphState`/`AgentApprovalInterrupt`，不暴露 raw snapshot。
4. 实现 injectable run recovery lock context，PostgreSQL integration 证明同 run 串行、
   不同 run 不相互阻塞、connection 关闭/进程终止后释放。
5. 重构 approval service 为“获锁后 re-read → classify → advance/reconcile → commit”；避免
   从首次请求和恢复请求复制两套验证逻辑。
6. 修改异常策略：只有 checkpoint 明确终态失败才写终态 FAILED；不确定 infrastructure
   失败保持可重试状态并返回安全错误。
7. 为相同 terminal submission 返回现有 snapshot；不同 fingerprint/revision/decision
   继续 409。
8. 加入两请求竞争、响应丢失、checkpoint 已推进、REQUEST_CHANGES 新 revision 和高影响
   Tool replay 测试。
9. 如最终确需 migration：生成唯一 R5 revision，验证 upgrade/downgrade/re-upgrade、命名
   约束和 metadata drift；不得修改已有 revision。
10. 更新架构/安全文档，运行完整 PostgreSQL recovery/idempotency 和质量门后停止。

## 最小测试

```powershell
uv run pytest -q tests/test_agent_run_service.py tests/test_agent_graph.py tests/test_agent_tool_execution_service.py
```

普通测试覆盖分类表、相同/不同提交、故障 seam、checkpoint DTO、未知状态 fail closed、
不回显 raw checkpoint，以及 terminal duplicate 返回 snapshot。

## PostgreSQL、Docker 与集成测试要求

必须在 guarded `postgres-test` + 官方 PostgreSQL checkpointer 上覆盖：

1. 第一次审批数据库 commit 后的精确故障点；
2. resume 调用前模拟进程终止，销毁所有 workflow/factory/Session 对象；
3. 重建后相同请求成功恢复；
4. 两个恢复请求竞争，只有一个推进 checkpoint；另一个在获得锁后返回相同最终 snapshot
   或明确的 in-progress 安全结果；
5. 相同 revision/fingerprint/decision/feedback 幂等；
6. 旧 revision、不同 fingerprint、不同 decision/feedback 和 foreign owner 被拒绝；
7. resume 成功且最终 product commit 完成，但 HTTP 响应丢失，重试返回既有 snapshot；
8. checkpoint terminal 而 product `RUNNING` 时只 reconcile，不重复 invoke；
9. 至少一个高影响 action 在崩溃/恢复/竞争矩阵中最多发生一次 domain write；
10. Tool execution `COMPLETED` replay、`IN_PROGRESS/UNKNOWN` fail closed 保持有效；
11. 最终 agent_run、approval、tool_execution 和 LangGraph checkpoint 状态一致；
12. restart/downgrade 操作只针对测试库，不读取 checkpoint 私有表。

如果有 R5 migration，还必须从 `e3b7c2d9a410` upgrade 到新 head、downgrade 回该 revision、
re-upgrade，并运行 PostgreSQL catalog 和 `alembic check`。

## 最终质量门

```powershell
uv lock --check
uv run pytest -q
docker compose up -d --wait postgres-test
uv run pytest -m "integration and not external_provider" tests/integration/test_agent_interrupt_resume.py tests/integration/test_agent_recovery.py tests/integration/test_agent_idempotency.py
uv run pytest -m "integration and not external_provider" tests/integration
uv run alembic current
uv run alembic heads
uv run alembic check
docker compose stop postgres-test
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
git diff --check
git diff --cached --check
```

## 客观验收标准

- [ ] 精确复现并修复“第一次 commit 后、resume 前”崩溃窗口。
- [ ] 相同已决定审批重试可恢复或返回最终 snapshot，不再只能 409。
- [ ] 两个恢复请求不能并发推进同一 run；锁在异常/进程终止后可恢复释放。
- [ ] revision、fingerprint、decision、feedback 和 owner 的任一不一致都零 resume/零 Tool。
- [ ] checkpoint pending、continuable、terminal 和 inconsistent 四类由公开 API 明确分类。
- [ ] resume 成功但响应丢失不会触发第二次高影响 domain write。
- [ ] 相同高影响 action 在所有重试/竞争路径最多执行一次；UNKNOWN 仍 fail closed。
- [ ] product run/approval/tool records 与 checkpoint 最终一致，无 raw checkpoint 暴露。
- [ ] 未引入 outbox/worker/queue；若有 migration，它只属于 R5 且完整往返通过。
- [ ] focused、完整 integration、迁移和最终质量门通过。

## 风险和回滚

- 风险：LangGraph 版本的 continuation 语义与假设不同。实现前 spike 是强制 gate；只用
  当前锁定版本公共 API。
- 风险：advisory lock 使用/连接池清理错误。必须用独立 transaction context，禁止
  session-level lock 泄漏到连接池。
- 风险：长 resume 占用额外 DB connection。任务记录实际最长持锁范围和 pool 影响；当前
  同步、单实例演示边界内接受，未来异步调度再考虑 outbox。
- 风险：checkpoint 已推进但产品状态未知。先 inspect/reconcile，不能盲发 Command。
- 风险：所谓 exactly-once 夸大。文档只声明已有 intent/idempotent replay 与 UNKNOWN
  fail-closed，不声明分布式 exactly-once。
- 回滚：只撤销 R5 facade、lock、service、测试、可能的独立 migration 和文档；migration
  downgrade 仅能在 disposable postgres-test 验证，不能自动用于开发数据。

## 停止条件

- 锁定 LangGraph 公共 API 无法区分 pending/continuable/terminal，或安全 continuation
  需要读取私有 checkpoint 表；
- 需要 background worker/outbox 才能满足恢复，而 owner 尚未重新选择方案；
- advisory transaction lock 不能在进程终止后可靠释放或会破坏 connection pool；
- 无法证明高影响 Tool 在响应丢失/竞争时最多一次 domain write；
- 需要修改 approval preview P0 或进入后续产品阶段；
- 新 migration 影响 R5 之外表或历史 revision；
- 候选暂存区被取消暂存、commit 或 push。

## 完成报告格式

- 修改文件和是否新增 migration；
- 两方案比较与最终选择是否仍成立；
- LangGraph 精确版本和公开 continuation 行为证据；
- 状态转换/重试矩阵及 advisory lock 生命周期；
- 每个强制崩溃、竞争、响应丢失和幂等测试结果；
- PostgreSQL、migration、完整 integration 和质量门；
- 残余 UNKNOWN/连接占用风险；
- 调用链和三个学习点；
- 明确停止，未开始 R6 或后续产品阶段。
