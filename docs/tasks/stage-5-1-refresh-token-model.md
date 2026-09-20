# Task 5.1 — Refresh Token 模型、索引与可逆迁移

## 状态与目标

- 状态：Completed / owner 已验收（2026-09-17）。模型、迁移和测试已实现，真实结果
  见文末；owner 随后授权继续，Task 5.2 按独立任务执行。
- 依据：[roadmap Stage 5](../roadmap.md#stage-5--deferred-authentication-hardening)。
- 预计实现时间：1～2 个专注小时；真实 PostgreSQL 验证属于实现验收。
- 目标：增加一个私有 `refresh_tokens` 表及对应 ORM 映射，用数据库约束支撑后续
  hash-only 存储、轮换和撤销，证明新迁移可升级、降级、再次升级。
- 开始实现的前置条件：owner 确认本契约；重新检查 Git 状态和迁移 head，保留已有修改。

## 已核对的基线

- 规划时 `main` HEAD 为 `3d58d40`，工作区与暂存区均为空。
- 规划时 10 个 Alembic revision 构成线性链，唯一 head 为 `e3b7c2d9a410`。
  Stage 5 虽在路线图中编号较早，新迁移必须接在当前 head 后，不插入历史链。
- 规划时 `app.models` 注册 9 个产品表，共享 `app.db.base.Base.metadata`；当时无 Refresh Token
  模型或表。LangGraph checkpoint 存储不属于该产品 metadata。
- `authenticate_user` 只校验密码、签发 Access Token，返回 `AccessTokenResponse`；
  本任务不改变登录响应、认证依赖或现有公开 API。
- `tests/integration/conftest.py` 已有 `validate_migration_test_target`，限制迁移目标为
  回环地址、专用 `stms_test` 数据库和用户、配置的测试端口（默认 5433）。
- 本文中的字段、摘要格式和约束是本任务提出的具体设计；roadmap 原文只确定任务范围，
  不能将这些设计写成已有功能或已经通过的验收结果。

## 开始实现前读取

- `AGENTS.md`、`CODEX_FASTAPI_LEARNING_TASK_SYSTEM.md`；
- `docs/requirements.md` 的认证要求、`docs/roadmap.md` 的 Stage 5、`docs/tasks/README.md`；
- `app/models/user.py`、`app/models/project.py`、`app/models/__init__.py`、`app/db/base.py`；
- `app/services/authentication.py`、`app/schemas/auth.py`；
- `alembic/env.py`、`alembic/versions/` 的完整 revision 链；
- `tests/test_user_model.py`、`tests/test_alembic_config.py`、
  `tests/test_integration_database_safety.py`、`tests/integration/conftest.py`、
  `tests/integration/test_user_migration.py` 及受新增表影响的 metadata 测试；
- `.github/workflows/ci.yml`、README 的专用 PostgreSQL 测试环境说明。

## 模型契约

类名 `RefreshToken`，文件 `app/models/refresh_token.py`，表名 `refresh_tokens`。
采用现有 SQLAlchemy 2 `Mapped` / `mapped_column` 同步模型约定，在 `app/models/__init__.py`
中导入并导出，确保 Alembic 获得同一份完整 metadata。导入模型不连接数据库。

| 字段 | SQL / Python 类型 | 空值与默认值 | 语义 |
| --- | --- | --- | --- |
| `id` | PostgreSQL UUID / `UUID` | NOT NULL；数据库 `gen_random_uuid()` | 单条凭据记录主键，不是 bearer credential |
| `user_id` | PostgreSQL UUID / `UUID` | NOT NULL；无默认值 | 所属 `users.id`，未来由可信认证流程确定 |
| `token_hash` | VARCHAR(64) / `str` | NOT NULL；无默认值 | SHA-256 摘要，64 个小写十六进制字符，仅内部使用 |
| `created_at` | TIMESTAMPTZ / `datetime` | NOT NULL；数据库 `CURRENT_TIMESTAMP` | 创建时间 |
| `expires_at` | TIMESTAMPTZ / `datetime` | NOT NULL；无默认值 | 到达此时间即过期，未来 Service 显式赋值 |
| `revoked_at` | TIMESTAMPTZ / `datetime \| None` | NULL；无默认值 | NULL 表示尚未撤销；非空表示不可再使用 |

所有时间映射为 `DateTime(timezone=True)`，应用按 UTC 写入和比较；不增加 Python 端
主键/时间默认值、数据库 trigger 或自动 `updated_at`。本表只需创建和撤销时间，
不加入 `status`、`is_active` 等可以推导且易发生不一致的状态字段。

### 摘要与生命周期边界

- 选定 `token_hash` 存储格式，是为了让模型、数据库约束和后续签发实现有一致契约。
  后续 Task 5.2 应对高熵随机令牌的原始文本 UTF-8 字节计算 SHA-256，禁止修剪或
  大小写归一化后再散列；本任务不实现生成、散列、验证或签发函数。
- 安全前提是后续签发使用密码学安全随机源并显式提供至少 32 随机字节。随机令牌与
  用户自选密码的威胁模型不同；密码继续使用 Argon2id，不能改成 SHA-256。
  Python 官方文档提供 [secrets 安全随机令牌接口](https://docs.python.org/3/library/secrets.html)
  和 [hashlib SHA-256 接口](https://docs.python.org/3/library/hashlib.html)；本项目的存储
  格式选择是设计决定，不代表官方文档规定 Refresh Token 必须采用此方案。
- 数据库只能验证摘要形状，无法证明输入确实经过散列；hash-only 数据流还需 Task 5.2
  的 Service/Repository 测试证明。本任务不得宣称已实现完整令牌安全。
- 后续可用性规则是 `revoked_at IS NULL AND expires_at > now`；等于过期时刻即无效。
  不把依赖当前时间的表达式放进 CHECK 或部分索引。
- Task 5.3 在同一事务中撤销旧行并插入新行，旧行保留以拒绝再次使用；本任务只提供存储
  基础，不实现并发控制或保证轮换原子性，不增加 `family_id`、`replaced_by_id` 或令牌链。
  整个令牌家族的重放撤销不是本契约的保证，若后续确有需要应另行设计。
- 退出、密码修改后的撤销语义及并发竞争由 Tasks 5.5 / 5.6 验收；短期 Access Token
  仍是无状态 JWT，撤销 Refresh Token 不会立即使已签发的 Access Token 失效。

### 具名约束与索引

| 名称 | 定义 | 目的 |
| --- | --- | --- |
| `pk_refresh_tokens` | PRIMARY KEY (`id`) | 唯一行身份 |
| `fk_refresh_tokens_user_id_users` | FOREIGN KEY (`user_id`) REFERENCES `users(id)`；默认 NO ACTION | 拒绝不存在的用户；保持当前不级联删除用户数据的约定 |
| `uq_refresh_tokens_token_hash` | UNIQUE (`token_hash`) | 所有用户之间摘要唯一，防止同一凭据绑定不同用户 |
| `ck_refresh_tokens_token_hash_format` | `token_hash ~ '^[0-9a-f]{64}$'` | 固定摘要格式，拒绝空值之外的非法形状 |
| `ck_refresh_tokens_expiry_after_creation` | `expires_at > created_at` | 有效期限必须为正 |
| `ck_refresh_tokens_revocation_not_before_creation` | `revoked_at IS NULL OR revoked_at >= created_at` | 撤销不能早于创建 |
| `ix_refresh_tokens_user_id` | 非唯一 B-tree (`user_id`) | 支撑后续按用户撤销及外键相关查找 |

`revoked_at` 可以晚于 `expires_at`，以允许已过期记录被显式撤销；不设置相反 CHECK。
同一用户允许多行，不能把 `user_id` 设为唯一。摘要唯一约束已有支撑索引，不重复建立
`token_hash` 索引；本任务没有清理任务或按过期时间扫描的用例，不预加 `expires_at` 索引。
不添加 ORM relationship、级联行为或用户删除接口。

## 迁移契约

1. 只新增一个 revision，`down_revision = "e3b7c2d9a410"`，`branch_labels` 和
   `depends_on` 为 `None`；revision ID 在实现时生成，不在本轮创建占位迁移。
2. 如果开始实现时 head 已变化，先核对新链并更新本契约依据；不能强行把旧 head 当父节点
   导致分叉。不得修改已验收的 10 个历史 revision。
3. `upgrade()` 只创建 `refresh_tokens`、其具名约束和用户索引；不改写用户、项目、任务、
   Agent、知识库数据，不修改 `vector` extension 或 LangGraph checkpoint 表。
4. `downgrade()` 只删除用户索引和 `refresh_tokens` 表及其所属约束；不使用 CASCADE 删除
   其他对象，不回退到 Stage 4，也不 drop/recreate 整个 schema。
5. “可逆”指 schema 能往返，不表示删除的 Refresh Token 行可恢复。降级会丢失该表中
   的所有记录；只在可丢弃的专用测试数据库验证，不能作为开发库或生产库的无损操作。
6. 迁移以本地 `sqlalchemy` / `alembic.op` 声明固定结构，不导入会随应用演进的 ORM
   模型；生成后逐项审查 DDL，不接受无关的 autogenerate 差异。

## 允许修改的实现文件

- 新增 `app/models/refresh_token.py`；注册到 `app/models/__init__.py`。
- 新增一个 `alembic/versions/` revision；现有 `alembic/env.py` 注册机制已足够，原则上无需修改。
- 新增 `tests/test_refresh_token_model.py`、`tests/integration/test_refresh_token_migration.py`。
- 更新 `tests/test_alembic_config.py` 的唯一 head、11 个 revision 和线性链断言；
  更新 `tests/test_user_model.py`、`tests/test_project_model.py`、`tests/test_task_model.py`、
  `tests/test_agent_run_models.py` 中精确表集合，加上新表，保留其他断言和历史 revision 测试。
- `tests/integration/test_stage11_rag.py` 的现行 head 断言同步为新 head；保留所有知识库、
  向量索引、所有权隔离及 grounding 行为断言。
- 实现验收通过后，在 `docs/roadmap.md`、本文件和 `docs/tasks/README.md` 记录真实结果。

规划轮只新增本文、更新 tasks README。实现轮也不增加 Repository、Service、Router、
公开 Schema、HTTP endpoint、token 工具函数、配置项或依赖。不改登录响应、Compose、CI、
`.env.example`，不实现 Tasks 5.2～5.7、清理作业、denylist、设备管理或 OAuth。

## 测试与验收矩阵

### 无数据库测试

- `RefreshToken.metadata is Base.metadata`；精确 6 列，类型、nullable、数据库默认值及
  具名约束/索引与上表完全一致；无明文 token 列、额外 relationship、Python 默认值。
- 导入模型与加载 Alembic metadata 不建立连接；完整产品表集合变为 10 张，历史表不变。
- revision 链只追加一个节点且只有一个新 head，父节点正确；保留完整历史链断言。
- 现有登录、公开 Schema、认证及 OpenAPI 回归保持原契约，不暴露 `token_hash` 或撤销字段。

### 真实 PostgreSQL

- 复用 migration target guard，在任何 DDL 前校验实际目标；只操作专用 `postgres-test`。
- 在当前父 revision 上建立少量合成的既有用户/项目哨兵记录，执行
  `parent → new revision → parent → new revision`，逐次检查 revision、表、列、默认值、
  PK/FK/UNIQUE/CHECK 及索引；证明既有对象和哨兵数据跨越新迁移保持不变。
- 验证数据库生成 UUID 和 timezone-aware 创建时间，显式过期时间正确往返。
- 验证同一用户可有多个不同摘要；不同用户的行保持各自 `user_id`。
- 拒绝重复摘要（同用户及跨用户）、不存在的用户、必填字段 NULL、非法摘要
  （空串、63/65 字符、大写或非十六进制字符）。
- 拒绝 `expires_at <= created_at` 和 `revoked_at < created_at`；允许 NULL 撤销时间、
  恰好创建时刻撤销，以及过期后撤销。用受控时间验证，避免依赖 sleep。
- 用户有关联令牌时删除用户被 FK 拒绝；相关试验只使用合成记录并回滚。
- 约束失败用独立事务或 savepoint 隔离，回滚后下一次合法写入仍可成功；不输出
  `IntegrityError` 原文、SQL 参数、摘要或完整凭据。
- 新迁移的 round-trip 仅回到它的父 revision。既有 integration 套件包含更深的历史
  downgrade，会删除测试库产品数据，因此完整套件仍必须串行运行在可丢弃的测试库。
- `finally` 尝试恢复到最新 head 并清除 settings 缓存；准确清理本测试哨兵，恢复失败时
  报告安全摘要，不掩盖原始测试失败。所有测试结束后再次确认 head 和无 metadata drift。

字段归属和 FK 不等于服务层访问控制。本任务没有资源查询 API，不能把跨用户行测试
说成已证明未来 refresh/logout 的授权隔离；后续任务继续验证可信身份和事务边界。

## 实现验收验证顺序

先执行新增模型测试和直接受影响的离线回归：

```powershell
uv run pytest -q tests/test_refresh_token_model.py tests/test_user_model.py tests/test_project_model.py tests/test_task_model.py tests/test_agent_run_models.py tests/test_alembic_config.py tests/test_integration_database_safety.py
docker compose up -d --wait postgres-test
```

按 README 配置专用 `STMS_TEST_DATABASE_URL` 和匹配的 `STMS_POSTGRES_TEST_PORT`，不在
报告中打印值。integration 使用进程级、明确 test-only、至少 32 字符的合成
`STMS_ACCESS_TOKEN_SECRET`；不读取真实 Provider 凭据、不修改 `.env`。先记录环境变量和
`postgres-test` 原始运行状态，结束时恢复；仅当本轮启动该服务时才停止它。

CLI migration 临时将应用数据库变量指向测试库，并在每条失败后停止：

```powershell
$task51PreviousDatabaseUrl = $env:STMS_DATABASE_URL
try {
    $env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL
    uv run python -c "import os; from tests.integration.conftest import validate_migration_test_target; validate_migration_test_target(os.environ.get('STMS_DATABASE_URL'))"
    if ($LASTEXITCODE -ne 0) { throw 'Migration target guard failed' }
    uv run alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Migration upgrade failed' }
    uv run alembic current
    if ($LASTEXITCODE -ne 0) { throw 'Migration current failed' }
    uv run alembic heads
    if ($LASTEXITCODE -ne 0) { throw 'Migration heads failed' }
    uv run alembic check
    if ($LASTEXITCODE -ne 0) { throw 'Migration drift check failed' }
} finally {
    $env:STMS_DATABASE_URL = $task51PreviousDatabaseUrl
}
```

然后运行以下命令，各条均检查真实退出码，失败即停止并处理。integration 执行时保持
`STMS_DATABASE_URL` 为原开发配置（或未设置），不能仍等于测试 URL；现有 fixture 会拒绝
两者相同或端口重合。测试内部临时切换迁移目标，不连接开发库。

```powershell
uv run pytest -m "integration and not external_provider" tests/integration/test_refresh_token_migration.py
uv run pytest -m "integration and not external_provider" tests/integration
# 重复上面的受保护 migration 块，确认套件结束后仍在 head 且无 drift。
uv lock --check
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
git diff --check
git diff --cached --check
```

若 `uv` 不在 PATH，使用本机已安装的可执行文件路径并记录；不为文档或测试随意升级依赖。
不得启动、停止、重建或迁移 `app` / `postgres-dev`，不得删除 volume、执行
`docker compose down -v` 或调用真实 Provider。任何未执行的检查必须明确列出原因，
本地通过不等于远程 GitHub Actions 已运行。

## 完成报告与停止边界

实现轮报告修改文件、模型/约束与迁移往返证据、各命令退出码、唯一 head 和 drift 结果，
以及是否保留原有暂存/未暂存改动。审查差异中的真实 secret、Token、摘要、Provider
凭据和无关修改；测试只使用合成数据，不在日志和报告中展示其值。

本任务没有新增 HTTP 调用链。需要讲清的实际存储加载链是：

```text
app.models 注册 RefreshToken → Base.metadata → alembic/env.py
→ 新 revision 的 upgrade/downgrade → PostgreSQL 约束与索引
```

其中 metadata 用于比较，真正执行 DDL 的是独立 revision。未来
`Router → Service → Repository → RefreshToken → PostgreSQL` 只作为方向，不算本任务产出。

三个学习点：摘要存储格式与签发安全的区别；数据库约束/索引分别解决什么问题；schema
可逆与数据可恢复的区别。模型和迁移验收后立即停止，等待 owner 确认，不开始 Task 5.2。
规划轮和实现轮均不自行 commit 或 push。

## 实现验收记录（2026-09-17）

- 新模型 `RefreshToken` 已注册到共享 metadata，产品表共 10 张；公开登录/API 契约未变。
- 新 revision `86cd95365562` 的父节点为 `e3b7c2d9a410`；现有 10 个 revision 未修改，
  当前线性链共 11 个 revision。`upgrade` 只新增表/约束/索引；`downgrade` 只删除本表。
- 启动时 Docker 引擎不可用；启动现有 Docker Desktop 后，确认三个 Compose 服务均为
  exited，再只启动 `postgres-test`。其目标为回环端口 5433、专用用户/数据库、tmpfs。
- 首次 upgrade 从空 schema 运行到新 head；专用往返测试证明父节点与新节点之间的
  upgrade/downgrade/re-upgrade，以及用户/项目哨兵完整行和 PostgreSQL extensions 保持不变。
- 21 项新增 integration 用受控时间、savepoint 和合成摘要验证所有约束；失败语句回滚后
  仍可写入合法记录。全套历史迁移与功能回归也通过，最终重新确认数据库在新 head。

以下为最终成功检查的真实结果，命令中的 `uv` 使用本机已安装的绝对可执行文件路径：

| 检查 | 退出码 | 摘要 |
| --- | --- | --- |
| 本文所列 7 个文件的 focused pytest | 0 | 41 passed |
| `docker compose up -d --wait postgres-test` | 0 | 测试服务 healthy |
| migration target guard | 0 | 首次迁移及全套测试后均通过 |
| `uv run alembic upgrade head` | 0 | 首次建库升级成功；测试后复查成功 |
| `uv run alembic current` / `heads`（分别执行） | 各 0 | 均为唯一 `86cd95365562` |
| `uv run alembic check` | 0 | 首次及测试后均无新 upgrade operations |
| focused PostgreSQL migration pytest | 0 | 21 passed |
| `uv run pytest -m "integration and not external_provider" tests/integration` | 0 | 93 passed |
| `uv lock --check` | 0 | 98 packages，锁文件未修改 |
| `uv run pytest -q` | 0 | 944 passed，94 deselected |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 252 files already formatted |
| `uv run mypy app tests alembic` | 0 | 227 source files，无问题 |
| `git diff --check` / `git diff --cached --check` | 各 0 | 无空白错误；暂存区为空 |
| `docker compose stop postgres-test` | 0 | 恢复原停止状态；三个服务均 exited |

中间失败也保留说明：Docker 初始连接检查退出 1（引擎未运行）；首次 mypy 退出 1，指出
测试中两处 SQLAlchemy 类型未充分收窄，补充 `DateTime` / `DefaultClause` 类型断言后通过；
首次完整 format check 退出 1，仅涉及 Stage 11 head 断言的换行，格式修正后通过。
这些不属于功能测试失败，定向和完整 PostgreSQL 测试均首次通过。

本轮未修改登录/刷新行为、配置、依赖或工作流，未调用真实 Provider、未操作开发库和
开发卷、未 commit/push，未触发或验证本次改动的远程 GitHub Actions。原有两份规划文档
在其基础上更新，所有改动保持未暂存。差异审查未发现真实凭据、完整 Token 或无关修改。

限制：本轮仅完成存储基础；摘要形状校验不能证明令牌已安全散列，签发、查询授权、
原子轮换和撤销仍待后续任务。降级删除的 Refresh Token 行不可恢复。以上为 Task 5.1
完成时的验收记录；owner 随后验收通过并授权独立执行 Task 5.2。
