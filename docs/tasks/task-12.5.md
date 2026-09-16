# Task 12.5 — 可复现演示、录制手册与测试/评估证据

## 状态

- Completed（2026-09-07）
- 验收依据：[roadmap Stage 12 的 Task 12.5 验收记录](../roadmap.md#stage-12--deployment-and-engineering-presentation)及 [demo/results.md](../demo/results.md)。最小测试 46 passed，普通测试 884 passed / 67 deselected，质量门通过；以下保留原始任务契约。
- 所属 Stage：Stage 12 — Deployment and engineering presentation
- 前置 Task：Task 12.4（README、演示资料、架构图均已验收）
- 后续依赖：Task 12.6 引用本 Task 的真实失败证据和测量边界。

## 目标

建立一份任何评审者都能按顺序复现的项目演示包：从锁定环境或 Compose 启动，到
health/OpenAPI、认证、Project/Task、知识资料、受审批 Agent 行为，以及离线评估和
CI/质量证据。同步给出简洁的屏幕录制脚本和一份带日期、命令、环境、结果来源的
验收快照。

本 Task 的主要交付成果是“可复现演示包”。默认演示必须离线、安全、无真实模型费用；
无法在默认路径展示的真实 Provider 行为应明确列为可选演示，不能伪造。

## 当前基线

- Task 12.1 提供 `docker compose up -d --build --wait app`，app 等待
  `postgres-dev` healthy、运行 Alembic head 后监听 8000，并通过 `/health/ready`
  验证数据库。
- Compose 不包含 JWT fallback secret。需要认证演示时，必须在当前进程或未提交
  `.env` 中设置至少 32 字符的 `STMS_ACCESS_TOKEN_SECRET`，不得记录其值。
- Task 12.2 已提供 `.github/workflows/ci.yml` 两个 jobs，但当前修改尚未 commit/push，
  因而没有对应远端 Actions run。只有未来真实 run 才能成为远端 CI 证据。
- Task 12.3 计划提供真实 API 示例和 `docs/demo/sample-syllabus.md` synthetic 资料；
  Task 12.4 计划提供系统和八节点 graph 图。执行本 Task 前必须确认它们实际存在且通过
  验收，不能根据本计划假设完成。
- 公开 Agent HTTP 入口包括 run 创建、owner-scoped snapshot、approval/resume 和 SSE；
  普通知识入口包括 upload、metadata 和 explicit index。
- 默认应用只支持真实 configured Provider adapter；普通测试和 offline evaluation 使用
  injected deterministic fakes。没有一个公开的“切换到 fake Provider”生产配置，演示
  不得新增这种后门。
- `evals/stage11/dataset.v1.jsonl` 有 39 个 synthetic cases；当前
  `baseline.v1.json` 记录 39/39 成功、安全门通过、四类准确率 1.0、违规/非预期写入/
  审批绕过/重复写为 0、恢复成功率 0.5、synthetic latency p95 1.0 ms。这些值是离线
  fake 基线，不是生产模型质量或真实网络延迟。
- Task 11 的 PostgreSQL、迁移和隔离证据已经存在；本 Task 不重复 destructive migration
  round trip。

## 范围

1. 新增一个统一演示入口文档，建议为 `docs/demo/README.md`（候选路径，先确认 Task
   12.3 实际目录），包含环境准备、预计时间、演示前检查、命令、预期结果和清理方式。
2. 设计一个 5～10 分钟录制脚本/分镜，依次展示：项目定位与架构图、一键启动、health/
   OpenAPI、一个 owner-scoped domain 流程、Agent 审批边界、RAG/citation 边界、测试与
   离线 eval 结果、限制说明。
3. 默认演示分成两条明确路径：
   - 无外部 Provider：Compose health/OpenAPI、domain API、静态 synthetic 文档、
     deterministic Agent/RAG/eval 测试证据；
   - 可选真实 Provider：只有 owner 明确提供临时环境变量、网络和费用授权时才运行，
     且不得录制 key、完整 prompt/model response 或敏感文档。
4. 使用 Task 12.3 的 synthetic 资料和 API 示例，不重新设计 payload 或复制第二套文档。
5. 记录一份结果快照，建议为 `docs/demo/results.md`（候选路径），包含日期、Git commit
   或“uncommitted”状态、Python/uv/Docker/PostgreSQL 版本、执行命令、退出码和简短摘要。
6. 展示 Task 12.2 CI 时：若 workflow 已 commit/push 且真实 run 成功，记录 run URL/
   commit SHA；否则清楚写“remote run unavailable”，不放绿色 badge。
7. 记录 Stage 11 baseline 的版本、39 cases 和安全门摘要，并明确 synthetic/fake 限制。
8. 提供安全停止/清理步骤。Compose 验收只停止本 Task 启动的服务；不使用 `down -v`、
   不删除 named volume、不对开发库 downgrade。
9. 可候选增加 `scripts/demo.ps1`，但仅当它显著减少手工错误：脚本只能编排公开 API
   和只读/安全命令，不直接写数据库，不内置密码/Token/key，不自动调用真实 Provider，
   不删除数据。若无法做到幂等和安全，保留文档式演示，不创建脚本。

## 非目标

- 不修改应用、Agent、RAG、Provider、数据库 schema、迁移、Docker 拓扑或 CI workflow。
- 不加入 production fake Provider 开关或绕过认证/审批的 demo 模式。
- 不生成或上传真实视频到外部平台；外部录屏和发布需要 owner 操作/授权。
- 不把 synthetic eval 指标描述成真实模型准确率、事实正确性、SLA 或线上成本。
- 不运行真实 Provider，除非执行该 Task 时 owner 单独明确授权网络、凭据和费用。
- 不新增 demo 用户删除、数据库 reset、seed 表或后台任务。
- 不撰写完整安全/改进/成本声明；属于 Task 12.6。

## 设计与接口约束

- 演示数据流必须复用公开入口：
  `demo client → FastAPI HTTP → Router → Service → Repository → PostgreSQL`；Agent 为
  `HTTP run → AgentWorkflow/LangGraph → Tool → Domain Service → PostgreSQL`。
- 身份必须来自 login 产生的 Bearer token；请求 payload 不得出现 caller-selected
  `user_id`、Session、SQL、vector 或 approval bypass 字段。
- 如果脚本需要保存 Token，只能保存在当前进程变量，不能写入结果文件、日志、命令行
  参数、仓库或录屏画面。
- demo account/password 必须由执行者通过进程变量提供或现场生成；仓库只允许明显
  placeholder。不得使用个人邮箱、真实资料或生产数据库。
- knowledge 文档始终是 untrusted data；录制说明不能暗示 citation 已验证事实真伪。
- 默认离线 Agent 证据来自现有 pytest/fakes/eval，不得把测试 fake 接入运行 app。
- 结果文件只保存安全版本、case count、aggregate metrics、命令与退出码，不保存完整
  prompt、model output、document text、Tool arguments、Token、数据库 URL 或隐藏推理。
- 数据库边界：可以启动 `postgres-dev` 做非破坏演示，或按已有安全守卫使用专用
  `postgres-test` 做测试；不得混用、downgrade 开发库或删除 volume。
- 外部服务：GitHub run 查询只读；录制上传、push 和 Provider 调用都需要明确授权。

## 预计修改文件

### 新增文件

- `docs/demo/README.md`（候选路径；统一演示 runbook）
- `docs/demo/results.md`（候选路径；安全、可重现的结果快照）
- `docs/demo/recording-checklist.md`（候选路径；仅当与 runbook 分离更清晰）
- `scripts/demo.ps1`（候选且可选；执行前证明安全和必要性）
- `tests/test_demo_assets.py`（候选；仅用于静态检查脚本无秘密/破坏命令且引用路径存在）

### 修改文件

- `README.md`（只增加演示入口链接，避免复制 runbook）
- `docs/roadmap.md`（全部验收后记录 Task 12.5 状态）

### 原则上不应修改的文件

- `.env.example` 和 Task 12.3 的 API 示例/synthetic sample 内容
- `docs/architecture.md` 和 Task 12.4 的图
- `app/**`、`alembic/**`
- `compose.yaml`、`Dockerfile`、`.dockerignore`
- `.github/**`
- `pyproject.toml`、`uv.lock`
- 现有业务、Agent 和 integration 测试

## 实施步骤

1. 核对 Task 12.3/12.4 acceptance、Git 状态、`.env` 存在性和 Task 12.2 是否已有远端
   Actions run；记录事实，不自动 commit/push。
2. 根据 README/OpenAPI/架构图选择 5～10 分钟主线，限制演示步骤和每步可见结果。
3. 编写无 Provider 默认 runbook；所有需要 secret 的输入使用进程变量和安全占位符。
4. 编写 Agent/RAG 离线证据段，使用现有 focused tests 与 baseline，不创建 production fake。
5. 设计可选 Provider 段，默认跳过；写清授权、费用、数据和录屏红线。
6. 仅在能保证无 secret、无直写、无破坏且可重复时创建 PowerShell helper；否则不创建。
7. 实际执行默认演示一遍，记录版本、命令、退出码和有界结果；对长日志只保留摘要。
8. 如远端 CI 可用，核对 workflow、commit 和 run 一致；否则如实记录缺口。
9. 执行静态安全扫描、最小测试和最终质量门；按启动范围停止服务，更新 roadmap 后停止。

## 测试策略

### 1. 最小相关测试

```powershell
uv run pytest -q tests/test_docker_assets.py tests/test_ci_workflow.py tests/test_main.py tests/test_agent_run_api.py tests/test_agent_evaluation.py tests/test_agent_evaluation_metrics.py
```

若新增 `tests/test_demo_assets.py`，验证：引用文件存在、脚本无硬编码 secret、无
`down -v`/downgrade/drop/truncate、无 Provider 调用、无直接数据库写入。

### 2. 集成测试

默认演示至少真实执行：

```powershell
docker compose config --quiet
docker compose up -d --build --wait app
```

验证 live/ready/OpenAPI 与选定 domain API 流程。只有需要引用完整 PostgreSQL integration
结果且 Task 11/12 证据不可信时，才按 `tests/integration/conftest.py` 安全守卫启动
`postgres-test`；禁止对 `postgres-dev` downgrade。结束只停止实际启动的服务。

### 3. 最终质量门

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

完整质量门只在最终运行一次。成功记录命令、退出码和摘要；失败保留关键错误和末尾输出。

## 验收标准

- [ ] 从 runbook 可在全新 checkout 完成环境检查、一键启动、health/OpenAPI 和至少一个
      authenticated owner-scoped domain 流程。
- [ ] 默认演示不需要外部 Provider、API key、网络模型调用或生产数据。
- [ ] 录制清单在 5～10 分钟内覆盖项目价值、架构、核心流程、安全边界和证据。
- [ ] Agent/RAG 展示使用现有 deterministic 测试/eval，并明确不是生产模型结果。
- [ ] results 文件记录日期、代码状态、版本、命令、退出码与有界摘要，可由他人复跑。
- [ ] Stage 11 baseline 的 case 数、指标和 gate 与 `baseline.v1.json` 精确一致。
- [ ] GitHub Actions 状态只在真实 commit 对应 run 存在时声明；否则明确标记 unavailable。
- [ ] 文档/脚本不含 secret、Token、个人数据、完整数据库 URL、Prompt/文档/模型敏感内容。
- [ ] 没有绕过 Router/Service、认证、owner filter、approval 或 idempotency。
- [ ] 没有删除 volume、downgrade 开发库、启动未声明服务或调用真实 Provider。
- [ ] 最小测试、实际默认演示和最终质量门通过。

## 停止条件

- Task 12.3 或 12.4 实际未完成；
- 公开 API/架构与演示要求存在重大冲突；
- 演示必须改变公共接口、数据库 schema、认证或审批策略；
- 需要新的密钥、Provider 费用、GitHub push、录屏上传或外部账户权限；
- 只能通过生产 fake 开关或直写数据库才能展示 Agent；
- 无法安全区分开发库与专用测试库；
- owner 必须在“仅离线证据”和“授权真实 Provider 演示”之间做关键选择。

## 执行完成后的报告格式

- 修改文件；
- 演示调用链/数据流与预计时长；
- 最小测试、实际演示和最终质量门；
- 远端 CI 与 Provider 是否实际使用；
- 未解决问题；
- 三个学习点；
- 明确停止，未进入 Task 12.6。
