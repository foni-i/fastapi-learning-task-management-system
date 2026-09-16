# Checkpoint Remediation R1 — CI runtime parity

## 状态

- Planned
- 类型：Stage 11～Task 12.6 candidate checkpoint remediation
- 对应审查 Finding：P1 — GitHub Actions integration job 缺少
  `STMS_ACCESS_TOKEN_SECRET`，真实 integration 命令无法通过
- 前置依赖：保持当前候选 checkpoint 暂存区不变
- 后续依赖：R2；全部 R1～R6 完成后才进入独立复审

## 根因

`.github/workflows/ci.yml` 的 integration job 只配置测试数据库端口和 URL。该 job
运行包含注册、登录、Bearer API、Project、Task、Agent 和知识库路径的完整 integration
目录，但没有满足 `app/core/config.py` 中 32 字符最小长度要求的 Access Token 签名值。

当前 `tests/test_ci_workflow.py` 只读取 YAML 文本并断言 job、命令和数据库隔离字段；它
既没有要求 integration job 提供测试签名值，也不会执行 workflow 的实际 integration
命令。因此，静态测试可以通过，而同一 job 在运行时出现
`AccessTokenConfigurationError`。

已复核的基线证据：在等价 CI 环境且不提供签名值时，integration 结果为
`47 passed, 19 failed`；加入进程级测试专用合成值后，同一命令为 `66 passed`。

## 目标

让本地可复现命令与 GitHub Actions integration job 使用相同、最小且明确的测试运行时
配置，使 workflow 在不读取任何真实 secret 或 Provider 凭据的情况下执行全部 66 个
integration 测试。静态契约继续负责配置防漂移，实际命令负责证明运行时可用性。

## 当前代码基线

- `.github/workflows/ci.yml` 有 `quality` 和 `integration` 两个 job。
- integration job 使用 PostgreSQL 17 + pgvector service、trust 认证和专用
  `stms_test` URL。
- `app/core/config.py` 允许应用无 JWT secret 启动，但 Token 签发/验证要求至少 32 字符。
- `pyproject.toml` 默认 pytest 排除 `integration` 和 `external_provider`。
- `tests/test_ci_workflow.py` 明确禁止 workflow 使用 `secrets.*` 和
  `STMS_MODEL_API_KEY`。
- 当前候选基线：HEAD `ed2d1ddf8643cd5d81ac444f7482396b205c8cab`；103 个暂存
  文件，11642 insertions、345 deletions；整改实现不得混淆该基线。

## 范围

1. 只在 integration job 的 job-scoped `env` 中加入测试专用合成
   `STMS_ACCESS_TOKEN_SECRET`。
2. 值必须明显表明只供 CI 测试使用，长度不少于 32 字符，不来自 `secrets.*`，不得在
   本地或生产文档中被描述为可用密钥。
3. 扩展 workflow 静态测试，证明变量只存在于 integration job，值满足长度和测试标识
   契约，quality job 不获得该变量。
4. 使用与 workflow 完全相同的 integration pytest 选择表达式执行一次真实 PostgreSQL
   验证；不能只以 YAML parse/substring 测试作为验收。
5. 保留 migration target guard、独立 test service、无 Provider 凭据和 action SHA pin。

## 非目标

- 不加入真实 GitHub secret、个人 secret、Provider key 或生产数据库凭据。
- 不为普通应用、Compose 或 `.env.example` 增加 JWT fallback。
- 不更改认证算法、JWT claims、TTL、issuer、audience 或 32 字符下限。
- 不拆分、跳过或 xfail 当前失败的 integration 测试。
- 不修改 quality job 的离线边界，不调用真实 Provider。
- 不 commit、push 或把本地结果描述成远程 GitHub Actions 成功。

## 预计修改文件

- `.github/workflows/ci.yml`
- `tests/test_ci_workflow.py`
- `README.md`（仅当现有 CI 运行说明需要补充“合成 job-scoped secret”边界）
- `docs/roadmap.md`（R1 完成后记录真实验收结果）

不预计新增运行时代码、迁移、依赖或测试 helper。若需要共享 CI 脚本，候选路径为
`scripts/verify-ci-integration.ps1`；只有证明它能被 Linux GitHub runner 和本地验证共同
复用且不会复制命令时才允许创建，否则继续直接运行 workflow 命令。

## 数据流或状态转换

```text
GitHub integration job env
  -> Settings(STMS_ACCESS_TOKEN_SECRET=<synthetic test-only value>)
  -> integration authentication helpers issue/validate JWT
  -> Router -> Service -> Repository -> guarded postgres-test
  -> 66 integration tests complete
```

该值只影响 integration job 进程，不进入 quality job、镜像、Compose、仓库 secret、
Provider 配置、测试输出或产物。

## 实施步骤

1. 再次复现未配置 secret 的 workflow 等价命令并保留有界失败摘要，不打印 Token 或 URL。
2. 在 integration job 顶层 `env` 增加清晰命名内容的测试专用合成值；不得使用 GitHub
   repository/environment secret。
3. 扩展 `tests/test_ci_workflow.py`：解析两个 job 的边界，断言 integration 值存在、长度
   合法且含测试用途标识，同时断言 quality job 不含 JWT secret。
4. 保留并复核 `secrets.`、`STMS_MODEL_API_KEY`、`postgres-dev` 等禁止项断言。
5. 启动并验证专用 `postgres-test`，先运行静态 workflow 测试，再执行 workflow 中完全
   相同的 migration 和 integration 命令。
6. 清除当前进程的测试变量并只停止 `postgres-test`；不得触碰开发库或 volume。
7. 执行最终质量门和 diff/秘密检查，记录本地证据，停止等待 R1 验收。

## 最小测试

```powershell
uv run pytest -q tests/test_ci_workflow.py tests/test_config.py tests/test_access_tokens.py
```

静态测试至少证明：

- integration job 存在测试专用 `STMS_ACCESS_TOKEN_SECRET`；
- 合成值长度不少于 32，且不是 `.env.example` 的生产占位建议；
- quality job 不含该变量、数据库变量或 Provider key；
- workflow 不引用 `secrets.*`；
- 实际 integration 命令仍是
  `uv run pytest -m "integration and not external_provider" tests/integration`。

## PostgreSQL、Docker 与集成测试要求

1. 未设置 `STMS_ACCESS_TOKEN_SECRET`，对专用测试库执行 workflow 等价 integration
   命令，记录 `47 passed, 19 failed` 或实现时真实的等价认证配置失败证据；若数量变化，
   必须解释差异，不能硬改测试期待旧数字。
2. 设置 workflow 中同一个测试专用合成值后执行：

```powershell
docker compose up -d --wait postgres-test
$env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL
uv run python -c "import os; from tests.integration.conftest import validate_migration_test_target; validate_migration_test_target(os.environ.get('STMS_DATABASE_URL'))"
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
uv run alembic check
Remove-Item Env:STMS_DATABASE_URL -ErrorAction SilentlyContinue
$env:STMS_ACCESS_TOKEN_SECRET = "<the-workflow-test-only-synthetic-value>"
uv run pytest -m "integration and not external_provider" tests/integration
Remove-Item Env:STMS_ACCESS_TOKEN_SECRET -ErrorAction SilentlyContinue
docker compose stop postgres-test
```

验收目标为 66 个 integration 测试通过；若测试清单增长，要求是当前完整选择集全绿并
单独说明数量变化。

## 最终质量门

```powershell
uv lock --check
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
git diff --check
git diff --cached --check
```

另做暂存/未暂存秘密模式检查，确认合成测试值没有被误写为应用默认值或真实凭据。

## 客观验收标准

- [ ] 无 secret 的 workflow 等价 integration 命令保留可复现失败证据。
- [ ] integration job 使用明确测试专用、长度合法、job-scoped 的合成值。
- [ ] 配置合成值后当前 66 个 integration 测试全部通过。
- [ ] 静态 workflow 契约会在变量缺失、过短、放错 job 或引用 `secrets.*` 时失败。
- [ ] quality job 不获得 JWT、数据库或 Provider 运行时配置。
- [ ] migration guard、PostgreSQL test identity 和 `not external_provider` 边界保持有效。
- [ ] 仓库、日志和输出不含真实 secret、完整 Token 或 Provider 凭据。
- [ ] 本地结果未被写成远程 GitHub Actions 已通过。
- [ ] 最小测试、完整 integration 和最终质量门通过。

## 风险和回滚

- 风险：合成字符串被读者复制到非测试环境。通过显式 `test-only` 命名、job scope 和
  文档警告降低风险。
- 风险：只锁定当前测试数量导致未来新增测试被忽略。命令始终选择完整 integration
  目录，数量只作为本次基线证据。
- 风险：把 JWT 配置误放到 quality job 会破坏其“无需秘密配置”证明；静态测试必须阻止。
- 回滚：仅撤销 R1 对 workflow、静态测试和说明文档的独立修改；不回退候选 checkpoint
  或其他整改任务。回滚后 CI 应恢复到已知失败状态，而不是伪装为通过。

## 停止条件

- integration 仍因 JWT 之外的生产缺陷失败；
- 需要真实 GitHub secret、Provider key、网络模型调用或生产数据库；
- 修复要求降低 JWT 安全下限或给应用/Compose 增加默认 secret；
- workflow job 边界无法从静态测试可靠区分；
- 专用测试库安全守卫失败或目标与开发库重合；
- 当前 staged candidate baseline 被改动、取消暂存、commit 或 push。

## 完成报告格式

- 修改文件；
- 无 secret 失败证据与配置合成值后的 integration 结果；
- workflow 静态契约及 quality/integration 配置边界；
- PostgreSQL、migration 和最终质量门命令、退出码、摘要；
- 远程 GitHub Actions 是否实际运行（默认应为否）；
- 未解决风险；
- 实际调用链和三个学习点；
- 明确停止，未开始 R2 或后续产品阶段。
