# Task 12.6 — 失败模式、改进路线、安全声明与成本边界

## 状态

- Completed（2026-09-07）
- 验收依据：[roadmap Stage 12 的 Task 12.6 验收记录](../roadmap.md#stage-12--deployment-and-engineering-presentation)。最小测试 77 passed，普通测试 884 passed / 67 deselected，40 个证据路径存在，质量门通过；以下保留原始任务契约。
- 所属 Stage：Stage 12 — Deployment and engineering presentation
- 前置 Task：Task 12.5（可复现演示和真实结果快照已验收）

## 目标

产出一份证据驱动的工程说明，集中回答项目会如何失败、当前防线是什么、哪些风险仍然
存在、下一步应按什么优先级改进，以及本地/CI/Provider/数据库的成本来自哪里。完成后，
评审者可以区分“已由代码和测试证明”“设计限制”“未来建议”，避免把学习项目包装成
未达到的生产系统。

本 Task 的主要交付成果是一个版本化的安全与限制说明，不实现修复或未来功能。

## 当前基线

- 认证已有 Argon2id、短期 HS256 access token、issuer/audience/type/expiry 校验和 owner
  查询；尚无 refresh rotation、logout revocation、password change、account recovery。
- Project/Task 使用可信 user identity、owner-scoped Repository 和复合数据库约束；写
 事务由 Service commit/rollback。
- Agent 使用 strict schemas、Tool allowlist、可信 runtime identity、approval fingerprint、
  durable approval、数据库 idempotency、checkpoint recovery 和安全 SSE。
- RAG 使用 owner-filter-before-rank、固定 RRF、有界 excerpt、citation allowlist 和
  untrusted grounding boundary；citation 只证明来源身份，不证明内容事实为真。
- tracing 只允许有界 metadata，默认 no-op，sink failure 被隔离；没有外部 tracing vendor。
- offline evaluation 是 39 个 synthetic fake cases，gate 当前通过；它不测真实 Provider
 漂移、线上延迟、事实正确性、恶意 PDF 引擎零日漏洞或生产负载。
- Docker Compose 是单 app 实例的本地开发部署；迁移在 app 启动前运行。没有多副本迁移
  协调、TLS/reverse proxy、secret manager、backup/restore、rate limiting 或云部署。
- Task 12.2 CI workflow 当前本地实现；是否已有远端 run 必须在执行时重新核对。
- Task 12.5 将提供实际 demo/test/eval 结果；本 Task 只能引用真实结果，不得复制计划值。

## 范围

1. 新增集中说明，建议路径 `docs/security-and-limitations.md`（候选路径，先确认无既有
   security 文档）。
2. 建立可验证的 failure-mode 表：触发条件、用户可见结果、现有防线、证据位置、残余
   风险和建议改进。至少覆盖配置/启动、数据库、认证、所有权、事务、Agent Provider、
   Tool/approval、checkpoint/retry、RAG/prompt injection、文件解析、SSE/tracing、CI 和
   evaluation。
3. 单独写安全声明，区分：
   - 已实现并有测试/约束证明的保证；
   - 当前 best-effort 控制；
   - 明确不保证的事项。
4. 列出按风险和依赖排序的改进路线；只引用 roadmap 已存在的 deferred/post-Stage 12
   项，新增建议必须标注 proposal，不能改变当前 roadmap。
5. 写成本说明：本机 CPU/内存/磁盘、PostgreSQL/pgvector、CI minutes、模型输入输出
   token、embedding 请求、重试、日志/存储。区分固定本地成本、随调用增长成本和未知项。
6. 如果引用供应商价格，执行时必须从官方来源核对、注明币种/日期/模型，且不得把价格
   写成长期保证；没有浏览需求时优先使用公式和成本驱动因素，不写易过期金额。
7. 引用 Task 12.5 的真实测试/eval/CI 证据，并明确 synthetic baseline 的外推限制。
8. README 只增加一个安全/限制入口链接；避免复制整份内容。
9. 全文执行 secret、夸大声明和路径检查；验收后更新 roadmap 状态。

## 非目标

- 不修复列出的 failure mode，不新增 refresh token、rate limiting、backup、TLS、云服务、
  tracing vendor、reranker、事实核验、MCP、多 Agent 或 Version 2。
- 不修改安全策略、Tool allowlist、approval、database constraints 或 migration。
- 不运行 penetration test、负载测试、真实恶意文件、真实 Provider 或生产数据。
- 不创建正式 SOC 2/ISO、隐私政策、法律合规或生产安全认证声明。
- 不提交供应商账户、账单、API key 或真实成本记录。

## 设计与接口约束

- 每项“已保证”必须链接到至少一个真实代码/迁移/测试/结果证据；没有证据时使用
  “限制”“风险”或“建议”，不能使用 guaranteed/production-ready。
- 错误处理描述必须与当前安全错误一致：不回显 credential、数据库 URL、SQL、完整
  prompt/document/model payload、vector 或内部 exception。
- 权限边界必须包含 authenticated identity、owner predicate、Tool argument exclusion、
  approval 和 idempotency；不得声称 JWT 单独提供资源所有权。
- RAG 安全声明必须明确 hostile document 仍可能含错误事实，citation 不是事实验证。
- evaluation 声明必须写明 deterministic synthetic fake、样本数和未覆盖分布。
- 成本模型建议使用变量公式，例如：模型成本由输入 token、输出 token、embedding 文本量、
  重试次数和供应商单价共同决定；不得从 synthetic token/latency 推算生产账单。
- 配置边界只引用现有 `.env.example`；不新增环境变量或 secret。
- 数据库和外部服务均不涉及写操作。本 Task 不需要 Docker、Provider 或网络，除非 owner
  明确要求核对当前官方价格。
- 与 Task 12.4 架构和 Task 12.5 结果术语一致，不另建第二套系统模型或指标。

## 预计修改文件

### 新增文件

- `docs/security-and-limitations.md`（候选路径）
- 可选 `tests/test_security_documentation.py`（候选；只验证敏感模式、必需章节和引用路径）

### 修改文件

- `README.md`（只增加一个文档入口）
- `docs/roadmap.md`（验收后更新 Task 12.6 状态）

### 原则上不应修改的文件

- Task 12.3 API 示例和 synthetic data
- Task 12.4 架构图
- Task 12.5 runbook/results/recording checklist
- `app/**`、`tests/**`（候选静态文档测试除外）、`alembic/**`
- `compose.yaml`、`Dockerfile`、`.github/**`
- `.env.example`、`pyproject.toml`、`uv.lock`

## 实施步骤

1. 核对 governing 文档、Task 12.5 结果、Git 状态和当前未实现列表。
2. 从配置、异常、认证、Repository、Service、Agent policy/state、RAG、tracing、evaluation、
   Docker 和 CI 建立“声明 → 证据”索引。
3. 编写 failure-mode 表，先记录事实和残余风险，再排列改进，避免从建议反向夸大现状。
4. 将安全声明分成 proved controls、best-effort boundaries、explicit non-guarantees。
5. 编写成本驱动与估算方法；只有当前官方来源可得且确有展示价值时才写日期化价格。
6. 检查与 requirements/architecture/roadmap/README 的限制清单一致。
7. 运行静态路径、秘密、夸大词和 Markdown 检查；执行最小相关测试与最终质量门。
8. 验收后增加 README 链接、更新 roadmap 状态并停止。

## 测试策略

### 1. 最小相关测试

```powershell
uv run pytest -q tests/test_security.py tests/test_access_tokens.py tests/test_agent_high_impact_policy.py tests/test_agent_grounding.py tests/test_agent_tracing.py tests/test_agent_evaluation_metrics.py
```

使用 `rg` 检查文档无 secret/token/key/private-key 模式，所有证据路径存在，且
“production-ready”“guaranteed”等声明均有明确证据或被移除。

### 2. 集成测试

不涉及。引用 Task 12.5 已记录的真实 integration/CI 证据，不为文档重复启动 Docker。
若证据缺失或与声明冲突，停止并降级声明，不自行运行 destructive 验收。

### 3. 最终质量门

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

最终完整门禁只运行一次。成功记录摘要；失败保留关键错误和末尾输出。

## 验收标准

- [ ] failure-mode 表至少覆盖配置、数据库、认证/所有权、事务、Agent/approval、RAG、
      文件解析、恢复/SSE/tracing、CI 和 eval。
- [ ] 每项已实现控制都引用真实代码、测试、迁移或 Task 12.5 结果。
- [ ] 文档明确区分 proved、best-effort 和 not guaranteed。
- [ ] citation/grounding 没有被描述为事实核验；synthetic eval 没有被描述为生产质量。
- [ ] 改进路线有优先级和依赖，不实现任何改进，也不扩大 roadmap。
- [ ] 成本说明区分固定/可变/未知成本，不含未经日期化核对的供应商价格。
- [ ] 文档不含 credential、完整 Token、数据库 URL、Prompt/document/model payload 或
      真实个人/账单数据。
- [ ] README 只增加入口链接，没有重复整份安全说明。
- [ ] 未修改应用、迁移、Docker、CI、依赖或现有行为。
- [ ] 最小测试、静态审查和最终质量门通过。

## 停止条件

- 需要改变已确认公共接口、安全权限、approval 或数据库 schema；
- 需要破坏性迁移、生产数据、渗透测试或新依赖；
- 需要新的密钥、供应商账户、费用、GitHub/云权限；
- Task 12.5 结果缺失或不能支持计划中的关键声明；
- roadmap 与真实限制存在重大冲突；
- owner 必须决定是否发布具体价格、法律声明或安全承诺。

## 执行完成后的报告格式

- 修改文件；
- failure mode、安全和成本证据流；
- 最小测试与最终质量门；
- 未解决问题；
- 三个学习点；
- 明确停止，未进入后续产品阶段。
