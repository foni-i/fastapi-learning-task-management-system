# Task 12.4 — 当前系统架构与 LangGraph 状态图

## 状态

- Completed（2026-09-07）
- 验收依据：[roadmap Stage 12 的 Task 12.4 验收记录](../roadmap.md#stage-12--deployment-and-engineering-presentation)。最小测试 41 passed，29 个引用路径存在，Ruff、格式、mypy、lock 和 diff 检查通过；以下保留原始任务契约。
- 所属 Stage：Stage 12 — Deployment and engineering presentation
- 前置 Task：Task 12.3（使用文档、术语、接口示例与 synthetic demo 资料已验收）
- 后续依赖：Task 12.5 的演示/录制手册引用本 Task 的架构图；Task 12.6 使用这些边界
  描述风险和安全保证。

## 目标

把 `docs/architecture.md` 从早期“planned/future”视角更新为当前实现的、可审查的
架构说明，并用可在 GitHub 渲染的 Mermaid 图展示系统边界、同步请求链、Agent/RAG
数据流、八节点 LangGraph、审批中断/恢复和持久化分离。完成后，读者能从图跳回真实
模块验证每条关系，而不会把计划能力误认为已实现能力。

本 Task 的主要交付成果是一套与代码一致的架构与状态图，不修改运行架构。

## 当前基线

- `docs/architecture.md` 已描述模块化单体、Router/Service/Repository、同步 Session、
  所有权和基础安全，但仍称 Agent path 为 future、source layout 为 planned，数据设计
  也没有完整覆盖 Agent product records、官方 checkpoint、knowledge documents/chunks、
  pgvector、grounding、tracing 和 offline evaluation。
- `app/main.py::create_app` 创建 FastAPI app 并挂载 `app/api/router.py`；v1 router 包含
  auth、users、projects、tasks、agent runs 和 knowledge documents。
- 领域调用链保持
  `Router → Service → owner-scoped Repository → synchronous SQLAlchemy → PostgreSQL`。
- `app/agent/graph.py` 固定八个节点：`analyze_goal`、`load_context`、`generate_plan`、
  `validate_plan`、`request_approval`、`execute_tasks`、`verify_result`、`summarize`；
  recursion limit 为 16。
- `app/agent/state.py` 保存严格、冻结、JSON 可序列化的 graph state；审批 fingerprint
  覆盖最终 proposal，写操作受 approval 与数据库幂等约束。
- RAG 链路由 knowledge upload/index、owner-filtered lexical/vector candidate queries、
  fixed RRF、`search_knowledge` Tool 和 bounded grounding 构成。
- Agent persistence 分为产品 run/approval/tool-execution records 与 LangGraph 官方
  PostgreSQL checkpoint；安全 SSE 与 no-content tracing 是独立投影。
- Compose 已有 `app → postgres-dev` 健康依赖；`postgres-test` 是独立测试服务。
- Stage 11 baseline 使用 39 个 synthetic cases，不能画成线上模型评估或事实正确性证明。

## 范围

1. 修订 `docs/architecture.md` 的 system context、实际 source layout、职责和依赖方向。
2. 新增或更新以下 Mermaid 图：
   - 部署/容器图：client → app → postgres-dev，以及隔离的 postgres-test/CI；
   - HTTP 分层请求图；
   - Agent Tool 与 Domain Service 调用图；
   - 知识上传、索引、混合检索、RRF、grounding 和 citation 验证数据流；
   - 八节点 LangGraph 状态图，包含 validation revision、approval interrupt/resume、
     reject/failure 和 terminal summary 分支；
   - 产品审计、checkpoint、SSE、trace 和 evaluation 的数据归属/禁止交叉边界。
3. 每个图旁给出关键模块路径和简短文字解释，使 Mermaid 渲染失败时仍可理解。
4. 更新数据设计表，列出现有 ORM 表和 pgvector extension，但不复制完整 migration SQL。
5. 明确同步 Session、Service transaction、Repository no-commit、可信 user identity、
   owner-filter-before-rank、untrusted document 和 approval/idempotency 边界。
6. 明确当前限制：无 refresh/logout/password change、grounded fact verification、外部
   tracing vendor、公共 search HTTP、MCP、多 Agent、Version 2 或生产云拓扑。
7. 只在验收结束后追加 Task 12.4 roadmap acceptance status。

## 非目标

- 不重构 Agent graph、state、nodes、tools、prompts 或 checkpoint。
- 不修改 Router/Service/Repository、数据库模型、迁移、Compose、Dockerfile 或 CI。
- 不引入图形生成、文档站点、Mermaid CLI、Graphviz 或新依赖。
- 不生成 PNG/SVG 截图作为唯一架构来源；GitHub 可渲染的文本图为规范来源。
- 不编写 demo/录制手册或测试结果页；属于 Task 12.5。
- 不撰写安全/成本评估；属于 Task 12.6。
- 不设计未来 MCP、多 Agent、Kubernetes 或微服务架构。

## 设计与接口约束

- 图只表示当前代码中的依赖。每个 node、Tool、Service、Repository 或存储关系必须能
  映射到真实文件、类或函数。
- HTTP 主链：
  `Client → FastAPI Router/dependency → Pydantic Schema → Service → Repository → Session → PostgreSQL`。
- Agent 主链：
  `Agent HTTP Service → AgentWorkflow/LangGraph node → strict Agent Tool → Gateway/Domain Service → owner-scoped Repository → PostgreSQL`。
- Grounding 主链：
  `load_context → search_knowledge Tool → lexical/vector Repository queries → deterministic RRF → bounded untrusted context → cited plan validation → approval fingerprint`。
- 不得画出文档直接控制 Tool、授权、审批或 system prompt 的边。
- 不得把 trace、SSE 或 product audit 画成保存完整 prompt、document、Tool arguments、
  model response 或 hidden reasoning。
- 数据库边界只描述已有迁移 head `e3b7c2d9a410` 和已有表；不涉及 schema 修改。
- 外部 Provider 必须画成可选适配器，并注明普通测试/离线 eval 使用 deterministic fake。
- Mermaid 节点标签保持简短；敏感字段、秘密示例和完整数据库 URL 不进入图。
- 与 README 术语保持一致；发现 Task 12.3 文档错误时停止并报告，不在本 Task 扩大修改。

## 预计修改文件

### 新增文件

- 原则上无。若 `docs/architecture.md` 因图过长确实失去可读性，
  `docs/agent-graph.md`（候选路径）可用于拆分，但执行前必须证明必要，并从 architecture 建立
  唯一入口。

### 修改文件

- `docs/architecture.md`
- `docs/roadmap.md`（验收后仅更新 Task 12.4 状态）

### 原则上不应修改的文件

- `README.md`、`.env.example` 和 Task 12.3 synthetic demo 资料
- `app/**`
- `tests/**`
- `alembic/**`
- `compose.yaml`、`Dockerfile`、`.dockerignore`
- `.github/**`
- `pyproject.toml`、`uv.lock`

## 实施步骤

1. 检查 governing 文件、Git 状态和 Task 12.3 acceptance；列出现有架构文档与代码的
   差异，不立即修改代码。
2. 从 router wiring、services/repositories、ORM metadata、Agent graph/state、RAG、
   checkpoint、SSE、trace 和 evaluation 代码建立“图节点 → 文件证据”表。
3. 先更新文字基线和 source layout，再逐图绘制；避免用图弥补错误文字。
4. 为八节点 graph 核对实际正常、审批、拒绝、修改重验、失败和总结边，不根据函数名
   猜测未存在的转换。
5. 核对持久化分类及敏感数据禁止流向，确保 trace/audit/checkpoint/SSE 不被混为一层。
6. 使用 GitHub Mermaid 语法的保守子集，并为每图添加文字替代说明。
7. 运行结构、链接和最小回归检查，审查 diff 只修改架构文档。
8. 验收通过后更新 roadmap 状态并停止。

## 测试策略

### 1. 最小相关测试

本 Task 主要使用只读结构检查：

```powershell
rg -n "ANALYZE_GOAL|LOAD_CONTEXT|GENERATE_PLAN|VALIDATE_PLAN|REQUEST_APPROVAL|EXECUTE_TASKS|VERIFY_RESULT|SUMMARIZE" app/agent/graph.py
rg -n "include_router" app/api app/main.py
rg -n "^class " app/models app/schemas app/agent/state.py
git diff --check
```

人工检查每个 Mermaid fence 成对闭合、节点 ID 唯一、所有引用路径存在。

### 2. 集成测试

不涉及。文档不改变运行行为，不启动 Docker、不连接 PostgreSQL、不调用 Provider。
若图与现有 integration 证据冲突，停止并报告实际不一致。

### 3. 最终质量门

若只修改 Markdown，运行最小现有契约和一次最终仓库检查：

```powershell
uv run pytest -q tests/test_main.py tests/test_agent_graph.py tests/test_agent_state.py tests/test_hybrid_retrieval.py
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

不因文档变更重复完整 integration。成功仅记录退出码和摘要；失败保留关键错误和末尾输出。

## 验收标准

- [ ] architecture 文档不再把已实现 Agent、RAG、checkpoint 或 tracing 描述为 future。
- [ ] 至少包含部署、HTTP 分层、Agent/RAG 数据流和八节点 graph 四类可渲染图。
- [ ] 八个节点名称、审批中断/恢复和终止分支与 `app/agent/graph.py` 一致。
- [ ] 每条主要调用链都附有真实文件路径，所有路径经结构检查存在。
- [ ] 图明确区分产品审计、官方 checkpoint、SSE、trace 和 offline evaluation。
- [ ] 图明确展示可信身份/owner filter/approval/idempotency 和不可信文档边界。
- [ ] 数据设计包含当前 knowledge/Agent 表和 `vector(1536)`，且未虚构新表或迁移。
- [ ] 当前未实现能力被明确标记，图中没有 MCP、多 Agent、云服务或 Version 2。
- [ ] 未修改应用、测试、迁移、Docker、CI、依赖或 Task 12.3 交付物。
- [ ] Mermaid/链接/路径检查及最终质量门通过。

## 停止条件

- roadmap 与实际 graph 或持久化模型存在重大冲突；
- 正确画图需要改变公共接口、graph 状态或数据 schema；
- 需要破坏性数据库迁移或新依赖；
- 需要新的密钥、账户、外部渲染服务或 GitHub 权限；
- Task 12.3 实际未验收，导致术语或入口不稳定；
- 某条关键 graph 分支无法从代码和测试唯一确定。

## 执行完成后的报告格式

- 修改文件；
- 图示的主要调用链和数据流；
- 最小检查与最终质量门；
- 未解决问题；
- 三个学习点；
- 明确停止，未进入 Task 12.5。
