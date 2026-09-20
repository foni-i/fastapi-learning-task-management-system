# Task 5.7 — 认证安全集成验收与文档收尾

## 状态与范围

Completed / 本地完整验证通过，owner 随后验收并授权独立的 checkpoint 提交准备。
Owner 验收 Task 5.6 后授权本任务，只执行 Stage 5 收尾。
基线为 49 个修改/未跟踪条目、暂存区为空；保留既有工作，不 stage/commit/push。
不引入新认证功能、依赖、迁移、Cookie、JWT denylist、限流、MCP 或其他后续阶段。

本任务审查既有实现并补足证据，不重复实现 Task 5.1～5.6。应用/迁移基线记录 SHA-256，
预计只新增安全验收测试与本任务文档，同步 README、架构、安全现状、roadmap 和索引。
前序文档中的验证数和故障记录属于历史快照，不替代本次真实结果。

## 验收证据对照

| 保证或边界 | 既有/新增测试文件与重点 |
| --- | --- |
| hash-only、约束、索引、可逆迁移 | `test_refresh_token_migration.py`：21 项真实迁移往返、唯一/外键/时间约束及原有数据保留 |
| 安全随机签发、提交前不交付 | `test_refresh_tokens.py`、`test_refresh_token_issuance.py`：格式、摘要、失败回滚与 owner |
| 轮换重放拒绝及原子性 | `test_refresh_token_rotation.py`（离线与 integration）：陈旧缓存、真实锁竞争及仅一个胜者 |
| HTTP 交付、退出及改密 | `test_refresh_http.py`：双用户、旧 Access 原 TTL、单凭据退出、全刷新吊销、并发次序、提交前故障 |
| 完整跨用例闭环 | 新增 `test_auth_security_acceptance.py::test_two_owner_complete_credential_lifecycle`：登录、刷新、退出、改密、重登录、隔离及日志无敏感信息 |
| 真实锁等待超时及恢复 | 新增 `test_real_user_lock_timeout_is_safe_and_retryable`：四个写入口，实际生产 5 秒锁超时、安全 503、状态不变、释放锁后重试成功 |
| 已提交后的结果不确定性 | 新增 `test_committed_write_cannot_be_undone_by_lost_acknowledgement`：四入口先真实 commit 再注入异常，证明 rollback 无法撤销已提交事务 |
| HTTP 解析/校验脱敏且不写库 | 新增 `test_real_http_validation_never_reflects_secrets_or_mutates`：四入口的敏感字段名、破损 JSON、无效编码；422/no-store，不回显内容 |
| CI 测试环境边界 | `test_ci_workflow.py`：integration-only 合成 test-only JWT 值、独立测试库、migration guard、排除真实 Provider、SHA pin |

所有新增用例标记 integration，普通 pytest 不连接数据库，也不运行真实 Provider。
测试仅清理本 fixture 创建的随机邮箱对应记录，先 refresh 再 user，不清空任意表。

## 必须诚实保留的失败语义

| 发生在真实 commit 之后的异常 | 持久状态 | 客户端恢复边界 |
| --- | --- | --- |
| login | 新摘要可能已入库但令牌未交付 | 重新登录产生独立凭据；未知摘要保留至过期或后续改密吊销，无自动清理/去重保证 |
| refresh | 旧凭据已消费，后继可能已入库但未交付 | 旧凭据重试 401，需重新登录；无 token-family 恢复机制 |
| logout | 所提交凭据已吊销 | 相同凭据重试 204，撤销时间不变 |
| change-password | 新密码及全部刷新吊销已提交 | 旧密码重试可能 401；用新密码重新登录，不将改密声明为幂等 |

测试模拟“提交后抛异常”，不是实际断网实验，也不声称枚举所有网络/进程失败。
所有 HTTP 失败都是固定安全响应；安全 503 并不等于数据库必然未写入。
Access JWT 仍到原 TTL 才失效；单凭据 logout 不撤销已产生的后继；公开部署仍需
TLS、限流、安全日志/代理和备份措施。不得将本地安全测试通过表述为生产安全认证。

## 复现顺序

1. 最小离线 Stage 5/CI 契约测试，再执行仅 postgres-test 的启动。
2. 采用 README 已有专用测试配置；Windows 当前使用进程级
   `STMS_POSTGRES_TEST_PORT=55433`，测试 URL、Compose 与 guard 必须同端口。
   不输出完整 URL；JWT 仅设进程级至少 32 字符的明显 test-only 合成值。
3. 迁移前把 `STMS_DATABASE_URL` 临时指向专用测试 URL，执行：

```powershell
uv run python -c "import os; from tests.integration.conftest import validate_migration_test_target; validate_migration_test_target(os.environ.get('STMS_DATABASE_URL'))"
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
uv run alembic check
```

每条失败立即停止，guard 未通过不得迁移。执行 pytest 前清除本进程的迁移用
`STMS_DATABASE_URL` override，保留 `STMS_TEST_DATABASE_URL`；开发目标保持原值。

```powershell
uv run pytest -m "integration and not external_provider" tests/integration/test_auth_security_acceptance.py --tb=line
uv run pytest -m "integration and not external_provider" tests/integration --tb=line
```

4. 套件后重新执行 guard 与四项 Alembic 命令；`uv lock --check`、完整离线 pytest、
   Ruff lint/format、`mypy app tests alembic`、两项 diff check、敏感信息与范围审查。
5. 恢复 postgres-test 停止并检查 compose ps；不启动 app/postgres-dev，不动开发卷。

数据库命令与 workflow 的 guard/迁移/marker 语义一致；本地 55433 与 CI 5433 是
显式环境差别。本任务不修改 workflow、不运行或宣称远程 Actions 通过。

## 实际执行结果

### 2026-09-19 本轮结果

新增 `tests/integration/test_auth_security_acceptance.py` 和本任务文件；同步 README、
architecture、security-and-limitations、roadmap、tasks README 与 Task 5.6 验收状态。
应用和迁移文件 SHA-256 与任务开始时一致；没有生产代码修复、模型/依赖/配置/CI 变更。
49 个既有改动条目全部保留，加上两个新文件共 51 个；暂存区仍为空。

| 命令/检查 | 退出码 | 真实结果 |
| --- | --- | --- |
| `uv run pytest -q tests/test_ci_workflow.py tests/test_refresh_tokens.py tests/test_refresh_token_model.py tests/test_refresh_token_repository.py tests/test_refresh_token_service.py tests/test_refresh_token_rotation.py tests/test_refresh_api.py tests/test_logout.py tests/test_password_change.py --tb=line` | 0 | 142 passed |
| `docker compose ps -a`（前/后） | 各 0 | app/postgres-dev 始终 exited；postgres-test 原停止、验证后恢复停止 |
| `docker compose up -d --wait postgres-test` | 0 | 进程级 55433，healthy |
| migration target guard（前/后） | 各 0 | 专用回环地址、stms_test、测试端口确认 |
| `uv run alembic upgrade head`（前/后） | 各 0 | 空 tmpfs 升级成功；套件后复查成功 |
| `uv run alembic current` / `uv run alembic heads`（前/后） | 各 0 | 唯一 head `86cd95365562` |
| `uv run alembic check`（前/后） | 各 0 | No new upgrade operations detected |
| `uv run pytest -m "integration and not external_provider" tests/integration/test_auth_security_acceptance.py --tb=line` | 0 | 新增 13 passed，含四次真实 5 秒锁超时 |
| `uv run pytest -m "integration and not external_provider" tests/integration --tb=line` | 0 | 162 passed，无真实 Provider |
| `uv lock --check` | 0 | 98 packages |
| `uv run pytest -q --tb=line` | 0 | 1078 passed，163 deselected |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 273 files already formatted |
| `uv run mypy app tests alembic` | 0 | 242 source files |
| `git diff --check` / `git diff --cached --check` | 各 0 | 无空白错误，index 仍为空 |
| 敏感模式扫描 | 0 | 已修改新增行、暂存新增行及未跟踪文件中无完整 JWT/带凭据 DB URL/私钥/常见 Provider key 命中 |
| `docker compose stop postgres-test` | 0 | 恢复 Exited (0) |

首次 mypy 退出 1：新增测试把 TestClient 的 Response 标为 httpx，而当前已锁定的客户端
实际返回 httpx2.Response；仅修正测试 import 后重跑通过，没有增加依赖或改变运行时。
新增 13 项真实测试首次通过；完整回归也通过。所有敏感信息扫描都是有界启发式，不能
据此宣称仓库/生产绝无秘密。人工审查新增测试的清理范围、错误输出和测试标记无阻塞项。

本轮未操作开发容器或卷，未修改 .env/Compose/系统端口，未清理 Docker socket。
没有 stage/commit/push，没有执行或宣称本次改动的远程 Actions 通过。历史 CI 引用
仍限于原 checkpoint。剩余风险保留于上面的失败矩阵和安全文档：Access JWT 原 TTL、
无 token-family 恢复/去重/自动清理、缺少限流、同用户锁争用、Docker 故障可能复发。
以上为 Stage 5 本地收尾验证完成时的记录，当时停止等待 owner 验收。
Owner 随后确认继续并明确同意 [checkpoint 提交准备](stage-5-checkpoint-preparation.md)；
Task 5.7 已验收，提交准备单独记录，不将后续检查混入本轮历史结果。

## 实际调用链与学习点

login Router → UserRepository 用户锁/验密 → `_prepare_issuance` → JWT → commit → 双令牌。
refresh Router → `_prepare_rotation` → 用户锁/凭据锁 → 条件撤销与后继插入 → JWT → commit。
logout Router → `logout_refresh_token` → 两级锁/单凭据更新 → commit → 204。
change-password Router/认证依赖 → `change_password` → 用户锁/重新验密 → 密码摘要更新、
owner-scoped 全刷新吊销 → commit → 204。Repository 无独立提交。

1. 分层单元测试之外，还需跨用例、跨请求、真实数据库的验收闭环。
2. 提交前回滚和提交后不确定是不同故障，不能把安全错误码当成回滚证明。
3. 证据表应同时列出保证与非保证，明确本地验证、历史 CI 与生产安全之间的边界。
