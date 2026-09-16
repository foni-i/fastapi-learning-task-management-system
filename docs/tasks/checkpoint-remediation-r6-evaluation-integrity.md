# Checkpoint Remediation R6 — Evaluation integrity

## 状态

- Planned
- 类型：Stage 11～Task 12.6 candidate checkpoint remediation
- 对应审查 Finding：P2 — evaluation observations 主要复制 `fake_script`，形成夹具自证
- 前置依赖：R5 完成并经 owner 验收；R6 必须评估最终 approval recovery 协议
- 后续依赖：独立提交前复审

## 根因

`EvaluationInput.text` 当前只通过 schema 校验，没有进入 goal、prompt、document、retrieval
或 graph。model/embedding/retrieval fakes 只按 case ID 和 `fake_script` 成败开关返回。
tool/citation profile 会调用局部 validator，但 approval respected 直接由枚举标签推导；
recovery、duplicate、token 和 synthetic latency 直接从 `fake_script` 复制到 evidence。

`_run_case` 再把这些值与同一行 expectation 比较，metrics 以 case outcome 或复制出的枚举
计算 accuracy/security/recovery 指标。因此改变恶意输入文本可能不改变结果，当前 39/39
主要证明 dataset parsing、fixture plumbing 和确定性，而不能支撑完整的 accuracy/security
gate 声明。

## 目标

保留快速、离线、确定性的 30～50 case baseline，但每个 observation 必须来自真实生产
代码路径的输出、调用记录、产品记录或 checkpoint 状态。`expectation` 仅用于判定，不向
runner 提供 observation。测试 fake 可以控制 Provider/embedding/clock/数据库 fixture，
但不能直接返回“recovered/deduplicated/approval respected”等最终判定。

## 当前代码基线

- dataset 版本 `stage11-eval.v1`，39 个 synthetic case，12 个类别。
- `EvaluationCase` 包含 `input`、`fake_script`、`expectation`。
- `app/agent/evaluation.py` 已有严格 loader、安全 case ID、bounded report。
- `app/agent/evaluation_metrics.py` 已有明确 denominator、rounding、threshold 和 baseline
  schema，可复用但需修正 observation 来源及不能证明的指标名称。
- 真实离线路径包括 goal analysis、prompt/Provider adapter Protocol、八节点 graph、Tool
  schema/dispatcher、grounding/citation validator、approval interrupt、R5 recovery 和 Tool
  execution idempotency。
- 普通 graph 测试可用内存 fake；recovery/idempotency 的真实证明依赖 postgres-test 和
  official checkpointer。

## 范围

1. 将 `EvaluationInput.text` 注入被评估的真实入口：普通案例至少进入
   `PlanningGoal.objective`；hostile document 案例进入真实 grounding context；retrieval
   案例作为真实 query/document fixture。
2. 将 `fake_script` 降级为依赖场景输入，例如 Provider 要返回的结构化候选、embedding
   向量、检索 fixture、故障点和 clock 序列。移除其中可直接决定 observation 的
   `approval_profile`、`recovery_outcome`、`duplicate_outcome`、`dry_run_write_count` 等字段。
3. 运行真实 `analyze_goal`、prompt builder、Provider response parsing、plan validation、
   Tool allowlist/argument validation、citation validation和 graph routing。
4. approval observation 从 graph 是否在 write 前 interrupt、无 approval 时 Tool 调用计数、
   matching approval 后实际执行记录计算，不从标签计算。
5. recovery/duplicate observation 使用 R5 的真实 service/checkpoint/idempotency 协议和
   PostgreSQL deterministic harness，依据最终产品/checkpoint状态及 domain write 计数计算。
6. expectations 保留为独立预期值，只在 observations 完成后比较；runner 禁止把 expectation
   对象传入 Provider/Gateway/observer。
7. fake Provider 必须实现生产 `ModelProvider` Protocol、接收真实 `ProviderRequest`、记录
   input/prompt version，并返回会经过生产 parser/schema 的结构化响应；不能只接收整个
   `EvaluationCase`。
8. fake embedding/retrieval 必须经过生产 Service/Tool boundary；若类别不需要某依赖，
   明确记录 `not_applicable`，而不是虚构成功。
9. token/latency 仅来自真实 ProviderResponse usage 与 injected clock 的实际调用；缺失保持
   missing，不从 expectation 推导。
10. 恶意输入 mutation 测试必须证明至少一个相关 observation/gate 会变化或失败。
11. 重新生成版本化 dataset/baseline。因语义改变，使用新版本
    `stage11-eval.v2`/`stage11-baseline.v2`，保留 v1 作为历史证据，不原地伪装等价。
12. 无法通过真实路径证明的 metric 必须移出 gate 或降级命名。例如若没有独立 extraction
    gold contract，就改为 `goal_propagation_accuracy`，不得继续称 extraction exact match。

## 非目标

- 不调用真实外部 Provider、使用模型 judge、训练模型或上传数据到 vendor eval 平台。
- 不把生产数据库或真实用户文档放入 dataset。
- 不允许 evaluation 通过公共生产配置启用 fake Provider。
- 不为了维持 39/39 修改 expectation 迎合实现结果；失败应暴露真实行为缺陷。
- 不使用 hidden reasoning、完整 prompt/document/model payload 作为 report/baseline。
- 不在 R6 重新设计 R5 approval recovery；发现 R5 缺陷应停止并回到独立 R5 correction。
- 不增加 Stage 12.7 求职材料或改变 proposal preview P0。

## 预计修改文件

- `app/agent/evaluation.py`
- `app/agent/evaluation_metrics.py`
- `tests/fakes/evaluation.py`
- `tests/test_agent_evaluation.py`
- `tests/test_agent_evaluation_metrics.py`
- `tests/test_agent_graph.py`
- `tests/test_agent_grounding.py`
- `tests/test_agent_high_impact_policy.py`
- `tests/test_agent_tool_execution_service.py`
- `tests/integration/test_agent_recovery.py`
- `tests/integration/test_agent_idempotency.py`
- `evals/stage11/README.md`
- `docs/security-and-limitations.md`
- `docs/demo/results.md`（仅更新明确标注的新 baseline 实测结果）
- `README.md`
- `docs/roadmap.md`（R6 完成后记录结果）

候选新增文件：

- `app/agent/evaluation_harness.py`：若真实 graph/recovery harness 与 schema/metrics 混在
  `evaluation.py` 会失去边界，则拆分。
- `tests/integration/test_agent_evaluation.py`：用于仅在 postgres-test 运行的 recovery/
  duplicate cases。
- `evals/stage11/dataset.v2.jsonl`
- `evals/stage11/baseline.v2.json`

v2 artifact 名称在实现前确认现有版本加载策略；不能覆盖 v1 后仍声明同一语义。

## 数据流或状态转换

```text
dataset scenario input.text + dependency fixture
  -> real PlanningGoal / hostile Grounding / retrieval input
  -> production prompt + Provider Protocol fake
  -> production parse/schema/graph/Tool validation
  -> optional R5 approval/recovery + idempotency on postgres-test
  -> independent observer reads outputs/call records/product state/checkpoint class
  -> EvaluationCaseEvidence observations
  -> compare with expectation
  -> metrics -> thresholds -> versioned baseline
```

禁止的数据边：

```text
expectation -X-> fake Provider/Gateway/observer
scenario label -X-> approval_respected/recovery_outcome/duplicate_outcome
fake_script -X-> final observation copy
```

## 指标证明矩阵

| 类别 | observation 来源 | 快速/数据库 |
| --- | --- | --- |
| goal/extraction | analyze_goal/prompt 中实际规范化值与独立 gold | 快速；不能证明则改名 |
| Tool name/arguments | 生产 Tool catalog、Pydantic schema、validator 结果 | 快速 |
| citations | 生产 grounding + plan validation 结果 | 快速 |
| plan bounds | Provider fake 输出经过真实 structured schema/validator | 快速 |
| hostile document | graph route、Tool call recorder、approval state | 快速 |
| approval bypass | interrupt/approval/Tool 调用顺序和 persisted decision | 快速 + R5 集成样本 |
| recovery | R5 崩溃 seam 后最终 run/checkpoint 状态 | PostgreSQL |
| duplicates | domain row/write count + Tool execution record | PostgreSQL |
| safe errors | 生产安全 error code/public report denylist | 快速/集成 |
| latency/tokens | injected clock 的实际跨度、ProviderResponse usage | 快速 |
| unintended writes | Gateway/domain recorder或测试表实际 delta | 快速 + PostgreSQL |

## 实施步骤

1. 先加 mutation test：只改变 `input.text` 为会触发真实不同 validation/hostile behavior
   的内容，证明 v1 当前 evidence 不变，从而固化缺陷。
2. 定义 v2 scenario schema，把 dependency stimulus 与 expectation 分离；删除可直接表达
   最终观察结果的 script 字段。
3. 建立生产 Protocol fake：真实 request 输入、严格 response 输出、usage/clock 记录；
   禁止 fake 接收 expectation。
4. 为快速类别建立真实 graph harness，所有 case 都让 `input.text` 进入 goal 或 grounding。
5. 从实际 exceptions、validated proposal、interrupt、Tool recorder 和 state 生成 evidence；
   删除 `_profile_evidence` 等标签到 observation 的捷径。
6. 在 R5 集成 harness 中执行 recovery/duplicate 子集，从数据库行数、execution status、
   checkpoint classification 生成 observation；确保与普通 baseline 的运行方式明确分层。
7. 重新定义不能证明的指标/threshold；记录每个 denominator 和 not-applicable 规则。
8. 生成 v2 39-case（或保持 30～50）的 synthetic dataset，先冻结 expectations，再运行
   runner；不得在看到失败后静默调阈值。
9. 连续运行两次快速 baseline 比较规范化输出；再运行 PostgreSQL 子集并构建最终安全
   baseline artifact。
10. 更新 Stage 11 eval、demo 和安全声明，明确 v1 历史限制与 v2 能证明/不能证明的范围。
11. 运行完整 Agent、integration、质量、秘密/敏感内容检查后停止等待独立复审。

## 最小测试

```powershell
uv run pytest -q tests/test_agent_evaluation.py tests/test_agent_evaluation_metrics.py tests/test_agent_graph.py tests/test_agent_grounding.py tests/test_agent_high_impact_policy.py
```

必须证明：

- 每个有效 case 的 `input.text` 到达真实 goal/prompt/grounding/retrieval 之一；
- fake Provider 收到生产 `ProviderRequest`，其输出经过真实 schema；
- expectation 不可被 dependency/observer 访问；
- 删除/反转 expectation 不改变 observation，只改变 pass/fail；
- 修改恶意输入至少可改变一个真实 observation 或 case outcome；
- Tool/citation/plan/approval 指标来自真实 validators/routes/call order；
- usage missing 保持 missing，latency 来自实际 clock 调用；
- 两次快速 run 输出顺序和规范化 baseline 完全一致；
- report/baseline 不含完整输入、prompt、document、Tool arguments、Token 或数据库 URL。

## PostgreSQL、Docker 与集成测试要求

R6 的快速 baseline 必须保持无 Docker、无 network、无真实写。recovery 和 duplicate 类别
必须另有 guarded postgres-test 集成运行，复用 R5 最终协议：

- 第一次产品 commit 后故障并重试恢复；
- 两恢复请求竞争；
- resume 响应丢失；
- matching revision/fingerprint replay；
- stale/different fingerprint 拒绝；
- 高影响 domain write 实际计数最多 1；
- Tool execution/product run/checkpoint 最终一致。

数据库 observation 来自公共 Repository/Service 或测试拥有的领域行，不读取 LangGraph
私有表。测试结束精确清理测试拥有数据，只停止 postgres-test。

## 最终质量门

```powershell
uv lock --check
uv run pytest -q
docker compose up -d --wait postgres-test
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

另外运行 v2 evaluation 两次，比较 normalized report/baseline，并用独立 mutation cases
证明输入变化能改变指标。若 gate 失败，保留失败，不得回写 expectation/threshold 掩盖。

## 客观验收标准

- [ ] `EvaluationInput.text` 实际进入每个适用案例的生产执行路径。
- [ ] expectations 只参与最终判定，绝不作为 observation 来源或传入 fake。
- [ ] approval、recovery、duplicate、write、token、latency observation 不再从 script 复制。
- [ ] 真实 graph/schema/Tool/citation/approval 代码被 harness 调用并有调用证据。
- [ ] R5 最终恢复协议由 PostgreSQL evaluation 子集实际覆盖。
- [ ] 恶意输入 mutation 能使相关 observation/case/metric 发生可解释变化。
- [ ] 快速 deterministic baseline 仍可在无网络、无 key、无数据库环境重复运行。
- [ ] 不能真实证明的 metric 已降级或重命名，文档不再称其为 accuracy/security gate。
- [ ] v2 dataset/baseline 版本与 v1 历史语义清楚分离，仍保持 30～50 synthetic cases。
- [ ] report/baseline 安全有界，无敏感/完整 payload。
- [ ] 快速、PostgreSQL integration、完整质量门和独立 mutation 检查通过。

## 风险和回滚

- 风险：真实 graph harness 使 baseline 变慢或脆弱。快速类别必须使用无 sleep 的 injected
  clocks/fakes；只有恢复/重复子集需要 PostgreSQL。
- 风险：fake Provider 仍通过 case ID 隐式自证。fake 只能按 scenario stimulus 构造原始
  Provider response，observation 必须由生产结果得出；mutation test 是最终防线。
- 风险：v2 指标低于 v1。不得调数据迎合，先判断是实现缺陷、case 不合理还是旧指标命名
  失真，并如实记录。
- 风险：baseline artifact 泄露输入。只保存 case ID、聚合和安全 observation。
- 回滚：独立撤销 v2 harness/schema/tests/artifacts 和文档，保留 v1 历史文件；不回退 R5
  或其他已验收整改。

## 停止条件

- 真实路径只能通过生产 fake 开关、真实 Provider key 或网络访问；
- expectation 仍必须参与构造 observation 才能运行；
- R5 尚未完成或 recovery/idempotency 结果不稳定；
- 需要读取官方 checkpoint 私有表或触碰开发数据库；
- 新指标/阈值必须由 owner 选择且会改变 release 声明；
- 不能证明某指标却要求继续使用 accuracy/security gate 名称；
- 候选暂存区被取消暂存、commit 或 push。

## 完成报告格式

- 修改/新增文件和 dataset/baseline 版本；
- 每类 case 的真实执行路径与 observation 来源矩阵；
- 删除的自证字段/逻辑及替代证据；
- input mutation、两次 deterministic run 和 gate 结果；
- R5 recovery/duplicate PostgreSQL evaluation 结果；
- 被降级/重命名的指标及依据；
- integration、migration 和最终质量门；
- 残余 synthetic/Provider 外推限制、数据流和三个学习点；
- 明确停止，进入独立复审前不 commit、不 push、不开始 Task 12.7。
