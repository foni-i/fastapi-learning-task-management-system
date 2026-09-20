# Stage 5 checkpoint 提交准备

## 授权、状态与基线

Owner 在 Task 5.7 验收后明确同意提交准备。范围仅为文档状态修正、全部未提交文件
审查、提交前复验和提交说明整理；不 stage、commit、push 或开始后续功能。
本记录不等同于新功能契约，也不授权修改 GitHub 设置或启动远程 Actions。

状态：Prepared / 2026-09-20 提交准备完成，等待 owner 确认；尚未暂存或提交。

- 基线分支 `main`，HEAD `3d58d40`，51 个状态条目：25 个已跟踪修改、26 个未跟踪文件。
- `git diff --cached --shortstat` 为空，暂存文件 0 个；没有沿用早期 checkpoint 的旧数量。
- 基线 `git diff --stat` 为 25 files / 846 insertions / 87 deletions，仅含已跟踪差异，
  不代表完整候选提交。完整审查同时读取所有未跟踪文件，不能遗漏新迁移或新测试。
- 本轮只更新 README、roadmap、security-and-limitations、tasks README、Task 5.7 状态，
  新增本文；没有编辑既有应用、测试、迁移。最终 52 个状态条目，暂存区保持为空。
- Task 5.1～5.7 文档中的测试数量、Docker 故障和当时未开始的后续任务均为历史记录；
  当前状态以各文件状态段、任务索引及本轮记录为准，不将历史 CI 当作当前证明。

## 完整候选文件清单

以下 51 个基线文件共同构成 Stage 5 候选，另加本文即 52 个。此清单只供 owner 后续
确认，不是已经暂存的清单。不使用 `git add .`，不在本任务写入 Git index。

### 应用（14）与迁移（1）

```text
app/api/v1/endpoints/auth.py
app/api/v1/endpoints/users.py
app/core/exceptions.py
app/core/refresh_tokens.py
app/main.py
app/models/__init__.py
app/models/refresh_token.py
app/repositories/refresh_tokens.py
app/repositories/users.py
app/schemas/auth.py
app/schemas/user.py
app/services/authentication.py
app/services/password_change.py
app/services/refresh_tokens.py
alembic/versions/86cd95365562_create_refresh_tokens.py
```

### 测试（23）

```text
tests/integration/test_auth_security_acceptance.py
tests/integration/test_authentication.py
tests/integration/test_refresh_http.py
tests/integration/test_refresh_token_issuance.py
tests/integration/test_refresh_token_migration.py
tests/integration/test_refresh_token_rotation.py
tests/integration/test_stage11_rag.py
tests/test_agent_run_models.py
tests/test_alembic_config.py
tests/test_authentication_service.py
tests/test_login_api.py
tests/test_logout.py
tests/test_main.py
tests/test_password_change.py
tests/test_project_model.py
tests/test_refresh_api.py
tests/test_refresh_token_model.py
tests/test_refresh_token_repository.py
tests/test_refresh_token_rotation.py
tests/test_refresh_token_service.py
tests/test_refresh_tokens.py
tests/test_task_model.py
tests/test_user_model.py
```

### 既有文档候选（13）

```text
README.md
docs/architecture.md
docs/requirements.md
docs/roadmap.md
docs/security-and-limitations.md
docs/tasks/README.md
docs/tasks/stage-5-1-refresh-token-model.md
docs/tasks/stage-5-2-refresh-token-issuance.md
docs/tasks/stage-5-3-refresh-token-rotation.md
docs/tasks/stage-5-4-refresh-http.md
docs/tasks/stage-5-5-logout.md
docs/tasks/stage-5-6-password-change.md
docs/tasks/stage-5-7-auth-security-acceptance.md
```

## 全量审查结论

这是本任务执行者的代码/测试/差异审查，不冒称独立 reviewer 复审或生产安全认证。
全量审查及本轮复验未发现需要修改应用代码的阻塞问题，边界与风险如下。

- **范围与兼容性：** 登录显式由两字段变为四字段，同时从只读变为写事务；严格旧客户端
  需要适配。新增 refresh/logout/change-password 与 Tasks 5.4～5.6 一致，没有后续功能。
- **身份与存储：** refresh/logout 以随机凭据摘要引导认证，不能传入 user_id；随后的
  更新均带可信 owner。改密身份来自 Access JWT 依赖，锁后重新验当前密码。持久化仅摘要；
  无原始令牌字段、公开 ORM 或摘要响应。内部签发入口不暴露给 HTTP/Agent 自选身份。
- **事务与并发：** Service 单次 commit，Repository 只查询/flush；JWT 与交付值在 commit
  前准备。统一用户行→凭据行锁顺序、populate_existing、条件更新，保持轮换单胜者，
  防止旧密码登录/刷新逃过改密吊销。失败回滚仅保证提交前场景，不能撤销已提交写入。
- **输入与错误：** SecretStr、严格字段/长度、固定认证错误、最多三项白名单校验错误，
  四入口 no-store/no-cache；普通 repr/log 不交付凭据。HTTP 成功交付是令牌输出例外。
- **迁移：** 只追加 `86cd95365562`，父节点 `e3b7c2d9a410`；六字段、具名约束及 owner
  索引一致，无历史 revision 改写。schema 往返可逆不等于被 drop 的凭据数据可恢复。
- **相关旧测试：** 精确表集合/head 更新是新增模型迁移的必要调整；原 RAG 断言保留。
  认证 fixture 仅先清理其随机测试用户的刷新记录，避免新增 FK 阻止原清理。
- **测试证据：** 单元、API、真实 PostgreSQL 覆盖成功/失败/所有权/时间边界/回滚/锁竞争；
  post-commit 注入明确是模拟异常，不冒称实际断网实验。新测试没有真实 Provider 请求。
- **配置与部署：** 本候选未改依赖、锁文件、CI、Compose、应用配置或 `.env.example`。
  quality job 无 JWT/DB/Provider 配置；integration 保留 job-scoped 合成 test-only JWT、
  独立 postgres-test、migration guard、排除 external_provider 及 action SHA pin。
- **文档修正：** 当前验收状态改为 Completed；保留历史证据。将过宽的“失败回滚”明确为
  “提交前失败回滚”，安全文档的认证错误边界补齐 logout/password-change。

已知限制不是本轮新增承诺：Access JWT 到原 TTL 才失效；单凭据 logout 不吊销后继或
其他登录；无 token-family 恢复、凭据交付去重、过期行自动清理、限流或生产容量证明。
同用户锁内验密/哈希会等待或超时；Docker 曾反复失效，测试端口仍用进程级 55433。

## 本轮验证

只从 Compose 的测试服务配置在进程内构造专用 URL，不输出完整 URL 或密码。
JWT 为进程级、明显 test-only 的合成值，不写配置文件。迁移守卫先于每轮 DDL；
CLI migration 临时设置应用 URL 为测试 URL，执行 pytest 前恢复原应用环境。
uv / Docker 均使用本机已安装的绝对可执行文件路径，不升级依赖。

2026-09-19 中断前，定向离线 142 passed，测试容器 healthy。9 月 20 日继续时，
首次 migration guard 退出 0，但 upgrade 无完成输出；只读复查发现测试服务已 stopped，
`pg_isready` 命令退出 1（service not running）。该迁移等待被中止，命令会话退出 1，
不计为通过。重新只启动 postgres-test 成功后，从 guard 开始完整复验。
没有从旧的 healthy 状态推断当前可用，也没有进行 socket/WSL/卷清理。

| 命令/检查 | 退出码 | 本轮真实结果 |
| --- | --- | --- |
| 定向离线 pytest（下方完整命令，中断前/最终复跑） | 各 0 | 均为 142 passed；9 月 20 日最终复跑 4.57 秒 |
| `docker compose ps -a`（中断前、恢复后、收尾） | 各 0 | app/postgres-dev 始终 exited；恢复后重新核实测试服务已 stopped |
| `docker compose exec -T postgres-test pg_isready -U stms_test -d stms_test`（恢复诊断） | 1 | 测试服务未运行，不计为数据库验证通过 |
| `docker compose up -d --wait postgres-test`（中断前、恢复后） | 各 0 | 仅测试服务 healthy，进程级 55433 |
| migration target guard（中止尝试、复验前/后） | 各 0 | 专用回环 stms_test 目标通过 |
| 首次 upgrade 等待 | 会话 1 | 测试服务停止期间无完成结果，中止后重新执行，不计为通过 |
| `uv run alembic upgrade head`（复验前/后） | 各 0 | 空 tmpfs 完整升级；套件后复查成功 |
| `uv run alembic current`（复验前/后） | 各 0 | `86cd95365562 (head)` |
| `uv run alembic heads`（复验前/后） | 各 0 | 唯一 head `86cd95365562` |
| `uv run alembic check`（复验前/后） | 各 0 | No new upgrade operations detected |
| `uv run pytest -m "integration and not external_provider" tests/integration --tb=line` | 0 | 162 passed，53.19 秒；含刷新迁移 21、HTTP 41、安全收尾 13 |
| `uv lock --check` | 0 | 98 packages |
| `uv run pytest -q --tb=line` | 0 | 1078 passed，163 deselected，15.67 秒 |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 274 files already formatted |
| `uv run mypy app tests alembic` | 0 | 242 source files，无问题 |
| `git diff --check` / `git diff --cached --check` | 各 0 | 无空白错误；LF/CRLF 提示不是错误，index 为空 |
| 全文件有界敏感模式扫描 | 1（待分类命中） | roadmap 中 6 处早已存在于 HEAD 的历史数据库 URL 示例；未输出原文 |
| 新增行、暂存新增行、全部未跟踪文件扫描 | 0 | 完整 JWT、带凭据 DB URL、私钥及常见 Provider/GitHub key 模式均无命中 |
| 候选清单、文档本地链接及未跟踪空白检查 | 各 0 | 52 项清单与实际状态一致；本轮六份文档无失效本地链接；未跟踪文件无行尾空白 |
| `docker compose stop postgres-test` | 0 | 恢复 Exited (0)；app/postgres-dev 未启动或重建 |

定向离线完整命令：

```powershell
uv run pytest -q tests/test_ci_workflow.py tests/test_refresh_tokens.py tests/test_refresh_token_model.py tests/test_refresh_token_repository.py tests/test_refresh_token_service.py tests/test_refresh_token_rotation.py tests/test_refresh_api.py tests/test_logout.py tests/test_password_change.py --tb=line
```

全文件扫描的 6 处命中逐项确认：都来自 HEAD 已有的 Stage 2 历史复现命令，指向本机
回环开发/测试端口，口令为通用示例字面量，不是本次添加的凭据。未读取真实环境文件
去比对，也不把示例口令视为安全部署口令。保留历史内容，不在本任务扩大成历史文档清理。
有界模式扫描不能证明绝无秘密；人工审查当前差异未发现真实 secret、完整 Token、
Provider 凭据、Repository 独立提交或无关功能变更。

最终仍为 main / `3d58d40`，25 个已跟踪修改 + 27 个未跟踪文件 = 52 个条目。
原 51 项均保留，增加的第 52 项只是本记录；`git diff --cached --shortstat` 仍为空。
没有更改 .env、系统端口、Docker 配置或开发卷，没有真实 Provider 调用、远程 CI、
stage/commit/push。复验与审查完成，等待 owner 确认下一步，不自动开始新任务。

## 建议提交说明（尚未执行）

```text
feat(auth): complete refresh token lifecycle and password change

- add hash-only refresh storage with a reversible migration
- deliver and rotate token pairs, revoke presented credentials on logout
- serialize password changes with login/refresh and revoke owned refresh tokens
- cover PostgreSQL concurrency, rollback and post-commit ambiguity
- document accepted Stage 5 behavior and security limits
```

后续若 owner 授权暂存/提交，须重新核对 HEAD、状态和精确清单；若出现新改动，先审查，
不能直接沿用本次结论。实际提交和推送为独立授权；当前没有本次改动的远程 CI 结果。

## 调用链与三个学习点

- login → `authenticate_user` → 用户锁/验密 → `_prepare_issuance` → JWT → commit → 双令牌。
- refresh → `refresh_authentication` → 用户锁/凭据锁 → 条件撤销/新摘要 → JWT → commit。
- logout → `logout_refresh_token` → 两级锁/单行吊销 → commit → 204。
- change-password → Bearer 依赖 → `change_password` → 用户锁/验密 → 密码与 owner 全刷新
  吊销 → commit → 204。底层统一 Repository → SQLAlchemy → PostgreSQL。

1. 普通 git diff 不含未跟踪文件，提交准备必须核对完整清单和 index，不能只看 shortstat。
2. 本地复验、owner 验收、Git 提交和远程 CI 是不同事实，需要分别记录。
3. 行锁和数据库原子事务可防竞争，却不能消除提交后响应丢失；恢复语义同样属于契约。
