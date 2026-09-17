# Security P0.1 — 有界审批 proposal preview 与 fingerprint 一致性

## 状态

- Completed（2026-09-17）；owner 已确认独立 endpoint、公开字段和 65,536-byte
  fail-closed 契约，运行时代码与验收已完成
- 前置条件：Stage 12 工程交付、R5 durable approval recovery 与 R6 evaluation integrity
  均已完成
- 本 Task 是独立安全加固，不开始 MCP、多 Agent、Version 2、Refresh Token 或生产部署

## 目标

让已认证 owner 在提交批准、拒绝或修改意见前，能够读取当前待审批 proposal 的完整、
有界、类型化公开预览，并证明预览、revision、fingerprint 和之后实际执行的 proposal 一致。
任何无法完整公开、超过总量边界、已经过期或与 checkpoint/产品记录不一致的 proposal
都必须失败关闭，不能通过截断、概括替换或隐藏字段继续审批。

## 当前事实

- `GET /api/v1/agent/runs/{run_id}` 只返回产品 Run、approval revision、fingerprint、decision
  与时间戳，不返回 proposal 内容。
- `POST /api/v1/agent/runs/{run_id}/approval` 要求调用者回传 revision 和 fingerprint，但调用者
  当前无法从公开 API 充分检查 fingerprint 所绑定的完整 proposal。
- LangGraph approval interrupt 只包含 plan summary、action name/count、revision 和 fingerprint，
  刻意不含 Tool arguments。
- 完整 `AgentPlanProposal` 已存在于受保护 checkpoint state；产品 `agent_approvals` 表只保存
  审批身份和结果。R5 恢复逻辑只使用 LangGraph 公开 `StateSnapshot` 接口。
- proposal 最多 3 个 write actions；batch create 每个 action 最多 10 个 Task。所有 write
  arguments 已由 `validate_tool_arguments` 和严格 Pydantic schema 校验，且不允许 `user_id`、
  Session、SQL、向量或任意额外字段。
- fingerprint 是完整 `AgentPlanProposal` 的确定性 canonical JSON SHA-256。

## 已选择的公共契约

### Endpoint

```text
GET /api/v1/agent/runs/{run_id}/approval-preview
```

该 endpoint 是只读 owner-scoped 查询，不接受 body、revision、fingerprint、`user_id` 或
checkpoint identity。身份只来自 Bearer authentication。

响应状态：

- `200`：当前 Run 正处于唯一、可验证的 pending approval，并返回完整公开 preview；
- `401`：未认证或 Token 无效；
- `404`：Run 不存在或不属于当前用户，两者响应相同；
- `409`：Run 不再等待该 proposal、revision 已过期或产品 approval 与 checkpoint 不一致；
- `422`：path UUID 无效；
- `503`：checkpoint/数据库不可用或公开 checkpoint 状态无法安全分类。

现有 `POST .../approval` 契约保持不变。客户端必须从 preview 取得 revision 和 fingerprint，
再将它们连同 decision 提交；GET 与 POST 之间发生状态变化时，POST 仍以 409 拒绝旧值。

### Response allowlist

顶层 `AgentApprovalPreview` 只包含：

- `run_id`；
- `revision`；
- `proposal_fingerprint`；
- 完整、已验证的公开 `planning_result`；
- 0～3 个按原顺序排列的类型化 `actions`。

每个 action 都包含 `action_key`、固定 `tool_name` 和与该 Tool 对应的专用 preview：

1. `create_task`：`task`，使用完整 `TaskCreate` 公开字段；
2. `update_task`：`task_id` 与 `changes`；`changes` 只序列化原 proposal 明确提供的字段，
   保留“未提供”与“明确为 null”的区别；
3. `batch_create_tasks`：`tasks`，包含 1～10 个完整 `TaskCreate`；
4. `delete_task`：`task_id`。

不得使用任意 `dict[str, object]` 作为公开 response field。实现应使用以 `tool_name` 为
discriminator 的四种冻结 Pydantic 模型；所有模型 `extra="forbid"`、隐藏校验输入，并复用
现有 Task 字段边界。响应序列化必须保留 proposal 的字段存在性，使 preview 可以无损重建
写操作语义。

明确禁止出现在 preview 中：

- `user_id`、Session、connection、SQL、vector/tsvector、checkpoint ID 或私有表字段；
- Provider request/response、完整 prompt、goal 原文、hidden reasoning 或异常文本；
- 文档正文、retrieved excerpt、embedding 或 citation 对应的私有内容；
- Token、secret、API key、Authorization header 或完整数据库 URL；
- Tool result、执行记录或未在 proposal 中出现的默认替换值。

`planning_result` 中已有的有界 plan step 和 citation ID 可以返回；citation ID 只是来源标识，
不携带 excerpt，也不保证事实为真。

### Exactness 与总量边界

- preview 必须从当前已经通过 `validate_plan` 的同一个 `AgentPlanProposal` 构建，不能从
  summary、数据库 audit 行或另一次模型调用重新生成。
- projector 必须先用生产 `validate_tool_arguments` 重新验证每个 action，再生成类型化
  preview；任一 action 无法投影时，workflow 在进入 approval 前失败关闭。
- preview 必须能够重建 fingerprint 输入的 proposal 语义；测试重新计算 SHA-256，必须与
  `proposal_fingerprint` 完全相同。
- 使用 UTF-8 canonical JSON 计算总大小；`MAX_APPROVAL_PREVIEW_BYTES = 65_536`。
  超过限制时返回固定安全错误并终止本次 proposal，绝不截断 description、列表、步骤或
  action arguments。
- 现有 action/batch/字段长度上限继续生效；本 Task 不放宽任何输入边界。

## 读取与一致性流程

建议调用链：

```text
GET approval-preview
→ Router authentication
→ Approval Preview Service
→ owner-scoped AgentRunRepository
→ run-scoped recovery lock
→ AgentWorkflow.inspect_durable(public StateSnapshot)
→ typed preview projection + fingerprint/size verification
→ public response
```

Service 必须按以下顺序执行：

1. 用 `(run_id, trusted user_id)` 查询 Run 和所属 Thread；foreign/missing 在打开 checkpoint
   前统一 404。
2. 取得同一 Run 最新 owner-scoped approval；要求 Run 为 `PENDING_APPROVAL`、approval 为
   `PENDING` 且 revision/fingerprint 与当前记录一致。
3. 使用与 R5 approval submission 相同的 run-scoped PostgreSQL transaction advisory lock，
   避免 preview inspection 与审批提交在同一 Run 上交错。
4. 通过 `AgentWorkflow.inspect_durable` 读取公开 snapshot；只接受唯一 pending interrupt。
5. 验证 interrupt 的 revision/fingerprint 与产品 approval 相同，再返回其类型化 preview。
6. GET 不 commit、不写产品表、不推进 checkpoint；结束时释放 lock，并关闭 workflow/
   checkpointer 资源。

锁释放后 preview 仍可能被并发请求变为过期，这是正常 optimistic boundary；后续 POST
必须继续校验 revision/fingerprint，并以 409 拒绝 stale submission。

## 实现范围

### 新增或修改

- `app/agent/nodes/approval.py`：定义类型化 preview contract、projection、大小检查，并让
  approval interrupt 携带完整 preview。
- `app/agent/graph.py`：公开 inspection 继续只返回验证后的 interrupt，不暴露 raw state。
- `app/schemas/agent_run.py`：定义 endpoint response wrapper；不把 preview 塞入 ORM-backed
  `PublicAgentApproval`。
- `app/services/agent_workflow.py`：新增只读 owner-scoped preview use case，复用恢复锁和
  checkpoint public inspection。
- `app/api/v1/endpoints/agent_runs.py`：新增唯一 GET endpoint 和 401/404/409/422/503 映射。
- `tests/`：schema、projection、node、Service、API/OpenAPI 测试。
- `tests/integration/`：真实 PostgreSQL/checkpoint 的 owner isolation、exactness、stale 和
  approve-exec 一致性测试。
- `README.md`、`docs/architecture.md`、`docs/security-and-limitations.md`、`docs/roadmap.md`：
  只在验收通过后更新真实行为和状态。

### 不修改

- 数据库模型、Alembic migration 和 `agent_approvals` 表；preview 只来自当前 checkpoint。
- 现有 approval POST body、decision 枚举和 R5 idempotent recovery protocol。
- Tool capability、Domain Service、Task API、Provider、prompt、RAG、SSE、trace 或 eval 指标。
- Compose、Dockerfile、CI、依赖和锁文件。

## 测试策略

### Schema 与 projection

- 四种 write Tool 的精确 preview 与字段 allowlist；
- update 的 omitted/null 区别和 action 原顺序；
- 0、1、3 actions 以及 batch 1/10 边界；
- extra field、未知 Tool、`user_id`、Session、SQL、vector 和非法嵌套字段拒绝；
- canonical round-trip 后 fingerprint 与原 proposal 相同；
- 65,536-byte 边界成功，65,537-byte 失败且没有截断输出；
- JSON 不含 prompt、goal、excerpt、document、credential 或 hidden reasoning。

### Service 与 API

- owner pending Run 返回 200 且 preview/revision/fingerprint 一致；
- missing/foreign Run 同为 404，foreign 路径不打开 checkpoint；
- decided、terminal、非 latest、revision/fingerprint/checkpoint mismatch 返回 409；
- checkpoint unavailable/inconsistent 返回固定 503，不泄露内部异常；
- GET 不 commit、不写记录、不推进 checkpoint；资源总是关闭；
- 未认证 401、非法 UUID 422；OpenAPI 只新增预期 GET 和显式 response schema；
- 现有 snapshot、approval POST、SSE 和日志仍不含 preview payload。

### PostgreSQL integration

- 在真实 durable interrupt 上读取 preview，再用同一 revision/fingerprint approve，证明实际
  Tool execution 的 action key、Tool name 和业务结果与 preview 一致；
- request-changes 后旧 preview 提交 409，新 revision/fingerprint/preview 可用；
- 两用户隔离和同 Run preview/approval 竞争；
- 不读取 LangGraph 私有表，不删除开发数据，只使用 guarded `postgres-test`。

## 验证顺序

```powershell
uv run pytest -q tests/test_agent_approval_nodes.py tests/test_agent_run_schemas.py tests/test_agent_workflow_service.py tests/test_agent_run_api.py
docker compose up -d --wait postgres-test
# 设置并验证专用 STMS_TEST_DATABASE_URL / STMS_DATABASE_URL 后：
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
uv run alembic check
uv run pytest -m "integration and not external_provider" tests/integration
docker compose stop postgres-test
uv lock --check
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
git diff --check
git diff --cached --check
```

迁移命令前必须复用 `tests/integration/conftest.py` 的 migration target guard。不得启动、停止、
重建或 downgrade `postgres-dev`，不得删除 volume，不得调用真实 Provider。

## 验收标准

- owner 能读取完整、类型化、总量有界且与 fingerprint 一致的当前 proposal。
- preview 与实际执行使用同一已验证 proposal；不存在第二次生成、静默截断或字段替换。
- foreign/missing、stale、mismatch、oversize 和 checkpoint failure 全部安全失败。
- preview 不包含受信身份、基础设施对象、凭据、prompt/reasoning、文档内容或 Tool result。
- GET 不产生产品写入或 checkpoint 推进，approval POST 与 R5 恢复语义不回归。
- 不新增 migration、依赖、后台任务、队列或新基础设施。
- focused、真实 PostgreSQL integration 和最终质量门全部通过。

## Owner 已确认的契约决策

Owner 已明确接受并完成以下三点：

1. 使用独立 `GET /approval-preview`，不改变现有 Run snapshot 与 approval POST schema；
2. preview 返回完整公开 plan 与精确 Task write 字段，认证 owner 可以看到 Task title/
   description，但永不返回文档正文、prompt、credentials 或内部 payload；
3. preview canonical JSON 使用 65,536 UTF-8 bytes 硬上限，超限 proposal 失败关闭且不截断。

## 验收结果

- focused schema/node/Service/API：76 passed；
- migration target guard、`upgrade head`、`current`、`heads`、`check`：全部成功，唯一
  head 为 `e3b7c2d9a410`，未新增 migration；
- `pytest -m "integration and not external_provider" tests/integration`：72 passed；
- ordinary full suite：940 passed，73 deselected；
- `uv lock --check`、Ruff lint、Ruff format、mypy：全部通过；
- 只启动并停止了 `postgres-test`；未启动 `app`/`postgres-dev`，未调用真实 Provider；
- 未 commit、push，也未运行远程 GitHub Actions。
