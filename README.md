# FastAPI STMS

FastAPI STMS 是 StudyFlow Agent 的分阶段后端学习项目。当前已完成 Stage 1 应用骨架、Stage 2 PostgreSQL/迁移基础设施、Stage 3 用户注册，以及 Stage 4 短期 Access Token 登录和当前用户能力。认证链路使用 FastAPI、Pydantic 2、SQLAlchemy 2 同步 Session、PostgreSQL、Alembic、Argon2id 和固定 HS256 JWT，并有真实 PostgreSQL 端到端测试。

当前尚未实现 Refresh Token、Token 持久化/轮换/撤销、退出登录、密码修改、管理员、密码找回、项目、任务或 Agent 功能。LangGraph、LLM 调用、Agent Tools、RAG、Checkpoint、HITL、Streaming、MCP 和多 Agent 都只是后续路线，不应把当前仓库描述成已经完成的 Agent 系统。

## 前置条件

- Git
- Windows PowerShell 5.1 或 PowerShell 7+
- 首次安装工具和依赖时可访问互联网
- `uv`；项目所需的 Python 3.14 由 `uv` 管理
- Docker Desktop，并启用 WSL 2 Linux 容器后端

导入应用、查看 OpenAPI 和调用 liveness 不需要 PostgreSQL；注册、登录、当前用户、readiness、迁移和数据库 integration 测试需要 PostgreSQL。登录和 Bearer Token 操作还要求配置安全的 Access Token secret。

## 安装 uv

在 PowerShell 中运行 uv 官方安装脚本：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

重新打开 PowerShell，然后确认安装：

```powershell
uv --version
```

## 获取项目

将 `<repository-url>` 替换为项目的实际 Git 地址：

```powershell
git clone <repository-url>
Set-Location FastAPI-STMS
```

## 恢复锁定环境

安装项目指定的 Python 版本，并严格按 `uv.lock` 安装运行和开发依赖：

```powershell
uv python install 3.14
uv sync --all-groups --locked
uv run python --version
```

`uv` 会在项目中管理 `.venv`。不要提交该目录。

## 配置环境变量

从可提交的示例文件创建本地配置：

```powershell
Copy-Item .env.example .env
```

- `.env.example` 只包含安全的示例值，可以提交到 Git。
- `.env` 属于本机配置，可能含有密钥，绝对不得提交。
- 当前示例支持运行环境、调试模式、API 文档开关、Access Token，以及本地开发/测试 PostgreSQL 服务配置。
- 数据库 URL 和密码只能来自环境变量或未提交的 `.env`；不要放入 README、`alembic.ini`、源码、日志或测试输出。
- `STMS_DATABASE_URL` 是应用和 Alembic 使用的数据库 URL；`STMS_TEST_DATABASE_URL` 只允许 integration 测试使用专用测试库。
- `STMS_ACCESS_TOKEN_SECRET` 没有应用默认值，必须在本机提供至少 32 个字符的随机 secret；不要使用或提交 `.env.example` 中的占位文本作为真实密钥。

没有 `.env` 时，应用仍能以安全默认值启动并提供无 Token 路径，但登录签发和 Bearer 校验不会使用不安全的默认 secret。

## SQLAlchemy 数据库基础设施

项目使用 SQLAlchemy 2 同步 API，各组件职责如下：

- `Base`（`app/db/base.py`）提供共享 declarative metadata；Stage 3 的 `User` ORM 映射到 `users` 表。
- `Engine`（`app/db/session.py`）管理连接池和数据库方言。它按进程缓存、同步运行，并配置有限的连接超时；创建 Engine 不会立即连接，首次 `connect()` 或 Session 执行 SQL 时才访问 PostgreSQL。
- `sessionmaker` 绑定上述 Engine，用于创建短生命周期的同步 `Session`。`get_session()` 每个请求 yield 一个 Session，并在 `finally` 中关闭；注册和当前用户写 Service 拥有 `commit`/`rollback`，Repository 只查询、`add`、修改和 `flush`。登录及当前用户读取不提交事务。
- readiness 探针使用短生命周期 `Connection` 执行 `SELECT 1`，通过上下文管理器在成功或查询异常时归还连接池。它不创建 Session，也不修改数据。

## PostgreSQL 开发与测试环境

`compose.yaml` 使用固定镜像 `postgres:17.6-bookworm`，并把两个数据库放在同一个明确命名的 `fastapi-stms` Compose 项目中：

| 用途 | Service | 主机端口 | 数据库 | 用户 | 数据策略 |
| --- | --- | --- | --- | --- | --- |
| 开发 | `postgres-dev` | `5432` | `stms` | `stms_dev` | named volume，停止/重建容器后保留 |
| 测试 | `postgres-test` | 默认 `5433` | `stms_test` | `stms_test` | `tmpfs`，容器移除后丢弃 |

`.env.example` 中的数据库账号和密码只用于本机开发演示，不是生产凭据。若复制为 `.env` 并修改，绝对不要提交 `.env`。

启动并检查两个数据库：

```powershell
docker compose config
docker compose up -d postgres-dev postgres-test
docker compose ps
docker compose exec postgres-dev pg_isready -U stms_dev -d stms
docker compose exec postgres-test pg_isready -U stms_test -d stms_test
```

Compose healthcheck 会在容器内使用实际的 `POSTGRES_USER` 和 `POSTGRES_DB` 调用 `pg_isready`。只有 PostgreSQL 接受连接后，服务状态才会变为 `healthy`。

开发库和测试库通过四层隔离避免误用：不同的 Compose service、不同主机端口、不同数据库/用户，以及不同存储。开发库使用 `fastapi-stms-postgres-dev-data` named volume；测试库使用容器内 `tmpfs`，重建测试容器不会清空开发 volume。integration 安全守卫还会拒绝开发库名称、端口复用、非 `*_test` 数据库和非 PostgreSQL URL。

停止并移除本项目容器和网络，同时保留开发数据 volume：

```powershell
docker compose down
```

不要为普通停止添加 `--volumes`，否则会删除开发数据库的 named volume。测试数据库使用独立的 `tmpfs`，不会复用或清空开发数据。

### 单独运行 PostgreSQL 集成测试

只启动专用测试数据库，不启动或修改开发数据库：

```powershell
docker compose up -d --wait postgres-test
$env:STMS_TEST_DATABASE_URL = "<dedicated-test-url-from-uncommitted-.env>"
uv run pytest -m integration
```

集成测试会在建立 Engine 前拒绝缺失、非 `postgresql+psycopg`、非 `*_test`、与 `STMS_DATABASE_URL` 相同、指向 `stms` 或与开发库复用主机端口的 URL。错误信息不会输出完整 URL 或密码。

若 Windows 把默认 `5433` 放入 TCP 排除范围，可只在当前 shell 或未提交的 `.env` 中把 `STMS_POSTGRES_TEST_PORT` 设为一个专用空闲端口，并让 `STMS_TEST_DATABASE_URL` 使用同一端口。Compose、连接测试和迁移安全守卫会拒绝端口不一致；不要为此修改 Windows 保留端口，也不要复用开发端口 `5432`。

### Baseline migration 往返验证

迁移往返会改变数据库 revision，只能对可丢弃的 `postgres-test` 执行。严禁把下面的 `STMS_DATABASE_URL` 改为开发数据库或端口 5432：

```powershell
docker compose stop postgres-test
docker compose rm -f postgres-test
docker compose up -d --wait postgres-test
$env:STMS_TEST_DATABASE_URL = "<dedicated-test-url-from-uncommitted-.env>"
$env:STMS_DATABASE_URL = $env:STMS_TEST_DATABASE_URL

uv run alembic upgrade head
uv run alembic current
uv run python -c "import os; from tests.integration.conftest import validate_migration_test_target; validate_migration_test_target(os.environ.get('STMS_DATABASE_URL')); print('validated dedicated migration test target')"
uv run alembic downgrade base
uv run alembic current
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run pytest -m integration tests/integration/test_migrations.py
```

`downgrade` 可能删除或变更 schema，绝不能在开发或生产数据库上把它当作普通检查运行。上面的 fail-fast 命令必须紧邻 downgrade，并严格验证驱动、本地主机、数据库 `stms_test`、用户 `stms_test`，以及 URL 与 `STMS_POSTGRES_TEST_PORT` 选择的主机端口一致；未显式配置时默认端口为 `5433`。测试 fixture 在每次自动 downgrade 前也执行相同校验。

验证后只停止测试服务；此命令不会停止 `postgres-dev`，也不会删除或修改开发 named volume：

```powershell
docker compose stop postgres-test
Remove-Item Env:STMS_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:STMS_TEST_DATABASE_URL -ErrorAction SilentlyContinue
```

## 启动应用

```powershell
uv run fastapi dev app/main.py
```

开发服务器默认监听 `http://127.0.0.1:8000`。保持该终端运行；验证完毕后按 `Ctrl+C` 正常关闭服务器。

### 存活检查

浏览器访问 `http://127.0.0.1:8000/health/live`，或在另一个 PowerShell 窗口运行：

```powershell
(Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:8000/health/live).Content
```

预期响应：

```json
{"status":"ok"}
```

`/health/live` 只表示 FastAPI 应用进程正在响应，不检查数据库或其他外部依赖。

### 就绪检查

`/health/ready` 会在收到请求时使用同步 SQLAlchemy Connection 执行轻量 `SELECT 1`：

- PostgreSQL 可回答时返回 HTTP 200 和 `{"status":"ok"}`。
- 数据库 URL 缺失、连接失败或查询失败时返回 HTTP 503 和 `{"status":"unavailable"}`。
- 503 响应不会包含完整数据库 URL、密码、驱动异常或堆栈。
- 应用导入和启动不会执行探针；`/health/live` 也永远不会访问数据库。

使用专用测试库手动观察真实状态转换：

```powershell
docker compose up -d --wait postgres-test
$env:STMS_DATABASE_URL = "<dedicated-test-url-from-uncommitted-.env>"
uv run fastapi dev app/main.py
```

保持应用终端运行，在第二个 PowerShell 中依次请求 health endpoint、只停止测试库、再次请求，然后恢复测试库：

```powershell
curl.exe -i http://127.0.0.1:8000/health/live
curl.exe -i http://127.0.0.1:8000/health/ready
docker compose stop postgres-test
curl.exe -i http://127.0.0.1:8000/health/live
curl.exe -i http://127.0.0.1:8000/health/ready
docker compose start postgres-test
docker compose up -d --wait postgres-test
curl.exe -i http://127.0.0.1:8000/health/ready
```

预期 readiness 为 `200 -> 503 -> 200`，liveness 在数据库可用和不可用时均为 200。完成后按 `Ctrl+C` 停止应用，并执行 `docker compose stop postgres-test`。

### OpenAPI 文档

- Swagger UI：`http://127.0.0.1:8000/docs`
- OpenAPI JSON：`http://127.0.0.1:8000/openapi.json`

如果将 `STMS_API_DOCS_ENABLED` 设为 `false`，上述两个文档入口会被关闭。

## 认证与当前用户 API

Stage 4 完成后公开以下六个方法/路径组合，且不应出现其他产品路由：

| 方法 | 路径 | 结果 |
| --- | --- | --- |
| `GET` | `/health/live` | 应用进程存活状态 |
| `GET` | `/health/ready` | 数据库就绪状态 |
| `POST` | `/api/v1/auth/register` | 创建最小用户并返回公开字段 |
| `POST` | `/api/v1/auth/login` | 验证凭据并签发短期 Access Token |
| `GET` | `/api/v1/users/me` | 返回已认证用户的公开字段 |
| `PATCH` | `/api/v1/users/me` | 只更新已认证用户的规范化邮箱 |

### 注册请求

请求体只允许 `email` 和 `password`，额外字段返回 422：

```powershell
$body = @{
    email = "  Learner@EXAMPLE.COM "
    password = "<example-only-12-to-128-character-password>"
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/api/v1/auth/register `
    -ContentType "application/json" `
    -Body $body
```

示例密码只用于本机演示，不是正式凭据。应用不会记录请求密码。

邮箱按固定顺序处理：

1. 去除整个输入的首尾空白。
2. 验证邮箱语法并规范化国际化域名；不执行 DNS 或可送达性检查。
3. 对规范化后的完整邮箱执行 Unicode `casefold()`。
4. 再次确认结果不超过 254 个字符。

数据库保存这个规范化值。PostgreSQL 命名唯一约束 `uq_users_email` 保证相同规范化邮箱不能重复，但数据库不会自动规范化任意原始邮箱。

密码规则为 12～128 个字符，并拒绝纯空白值。应用不 trim、不截断、不 casefold、不改变大小写，也不要求任意复杂字符组合。明文只在校验和哈希所需的最短范围存在，数据库只保存 Argon2id hash。

### 注册响应

成功返回 HTTP 201，且字段白名单严格为：

```json
{
  "id": "019c3d61-f4f7-7b1f-9e28-d31bc7359f47",
  "email": "learner@example.com",
  "created_at": "2026-08-28T10:30:00Z",
  "updated_at": "2026-08-28T10:30:00Z"
}
```

- `id` 是 PostgreSQL 生成的 UUID。
- `email` 是规范化后的存储值。
- 时间戳是 timezone-aware UTC ISO 8601。
- 响应、OpenAPI 和公开 Schema 均不包含 `password` 或 `password_hash`。

相同规范化邮箱返回 HTTP 409：

```json
{
  "detail": "An account with this email already exists"
}
```

非法邮箱、弱密码或额外字段返回 HTTP 422。密码相关错误中的原始输入会被遮蔽；409 和 422 均不会返回密码、hash、SQL、约束诊断或原始数据库异常。

### 注册事务与并发

注册调用链为：

```text
POST /api/v1/auth/register
-> UserRegistrationRequest
-> Router 注入请求级同步 Session
-> Registration Service
-> User Repository
-> PostgreSQL
-> PublicUser 或安全错误
```

- Router 只拥有 HTTP 解析、依赖、状态码和响应模型。
- Schema 负责请求验证、秘密感知输入和公开字段白名单。
- Service 协调规范化、密码策略、Argon2id 和完整写事务；成功 `commit`，异常 `rollback`。
- Repository 只执行邮箱查询、`add` 和 `flush`，不独立提交或回滚。
- 请求依赖在响应路径结束后关闭 Session。
- 应用层提前查询提供常规重复邮箱错误；并发请求都通过早期查询时，`uq_users_email` 是最终防线。真实双 Session 测试证明同一规范化邮箱只能一个请求返回 201，另一个返回安全 409。

### 登录与Access Token

`POST /api/v1/auth/login` 的请求体严格只允许 `email` 和 `password`。邮箱复用注册时的规范化规则；密码保存在 `SecretStr` 边界内，不 trim、不截断、不 casefold，也不修改大小写。登录只限制密码为 1～128 个 Unicode 字符以约束认证工作量，不重新应用注册时的 12 字符最小长度规则。

```powershell
$loginBody = @{
    email = "learner@example.com"
    password = "<local-example-password>"
} | ConvertTo-Json

$login = Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/api/v1/auth/login `
    -ContentType "application/json" `
    -Body $loginBody
```

成功返回 HTTP 200，响应字段严格为：

```json
{
  "access_token": "<access-token>",
  "token_type": "bearer"
}
```

不存在的邮箱和错误密码使用完全相同的 HTTP 401：

```json
{
  "detail": "Invalid email or password"
}
```

响应同时包含 `WWW-Authenticate: Bearer`，不会透露账号是否存在，也不会返回密码、hash、SQL 或数据库异常。

Access Token 契约如下：

- 算法固定为应用选择的 `HS256`，不会根据未验证的 Token header 选择算法。
- `STMS_ACCESS_TOKEN_SECRET` 无默认值且至少 32 个字符；默认 TTL 为 15 分钟，允许范围为 1～60 分钟。
- issuer 默认 `fastapi-stms`，audience 默认 `fastapi-stms-api`。
- 必需 claims 为 `sub`、`type`、`iat`、`exp`、`iss` 和 `aud`；`sub` 是用户 UUID 文本，`type` 必须为 `access`。
- 校验固定算法、签名、过期时间、type、issuer、audience 和 UUID subject 后，Token 才能影响数据库查询。
- Access Token 不存入数据库。当前没有主动撤销或 denylist，只能依靠短有效期到期；Refresh Token、轮换和撤销属于延后认证增强。

### Bearer当前用户

受保护请求使用明显的占位 Token，不要把真实 Token 写入文档、源码或日志：

```powershell
$headers = @{ Authorization = "Bearer <access-token>" }
Invoke-RestMethod `
    -Method Get `
    -Uri http://127.0.0.1:8000/api/v1/users/me `
    -Headers $headers
```

`GET /api/v1/users/me` 成功返回 HTTP 200，字段白名单严格为 `id`、`email`、`created_at` 和 `updated_at`。它不返回密码、`password_hash`、Token 或内部 claims。

缺失、格式错误、篡改、过期、错误 type/issuer/audience、非法 UUID subject 或数据库中不存在的用户均返回同一个 HTTP 401 和 Bearer challenge：

```json
{
  "detail": "Could not validate credentials"
}
```

Bearer dependency 先完整验证 Token，再通过同步 Session 和 Repository 按 UUID 查询用户。Token 中不存在客户端可覆盖的用户资料或权限字段。

### 当前用户邮箱更新

`PATCH /api/v1/users/me` 请求体严格只允许 `email`：

```powershell
$updateBody = @{ email = "  Updated@EXAMPLE.COM " } | ConvertTo-Json
Invoke-RestMethod `
    -Method Patch `
    -Uri http://127.0.0.1:8000/api/v1/users/me `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $updateBody
```

- `password`、`user_id` 和其他资料字段均被拒绝。
- 邮箱复用 Stage 3 规范化规则；相同规范化邮箱是幂等 HTTP 200，不查询、不写入，也不提交事务。
- 更新成功返回相同的 `PublicUser` 四字段，不改变 UUID、`password_hash` 或 `created_at`。
- 原邮箱不再能登录，新邮箱可以登录；先前签发的 Token 仍通过不变的 UUID subject 解析同一用户。
- 另一用户已占用该规范化邮箱时返回安全 HTTP 409：`{"detail":"An account with this email already exists"}`。
- 应用层提前查询改善常规冲突路径；两个请求同时通过查询时，PostgreSQL 命名约束 `uq_users_email` 是最终防线。Service 回滚失败事务，Router 只映射已确认的邮箱冲突。

### Stage 4分层与事务

```text
HTTP request
-> Pydantic Schema
-> Router / Bearer dependency
-> Service
-> Repository
-> synchronous SQLAlchemy Session
-> PostgreSQL
-> explicit public response or safe error
```

- Router 负责 HTTP 解析、依赖、状态码和响应 Schema。
- Schema 负责字段白名单、秘密感知输入和规范化。
- Authentication Service 验证密码并签发 Token，不开启写事务。
- Bearer dependency 验证 Token 后按 UUID 解析数据库用户。
- Current-user Service 拥有邮箱写事务的 `commit`/`rollback`。
- Repository 只查询、创建/修改实体和 `flush`，没有 HTTP 概念，也不 `commit` 或 `rollback`。
- 请求依赖创建并关闭每个同步 Session。

## 测试和质量检查

运行普通测试：

```powershell
uv run pytest
```

pytest 默认排除带有 `integration` marker 的测试，因此普通测试不创建数据库 Engine、不连接 PostgreSQL，也不要求 Docker 正在运行。集成测试必须使用上一节的显式 `-m integration` 命令单独运行。

运行 Ruff 静态检查和格式检查：

```powershell
uv run ruff check .
uv run ruff format --check .
```

运行 mypy 严格类型检查：

```powershell
uv run mypy app tests alembic
```

检查依赖锁和 Git diff：

```powershell
uv lock --check
git diff --check
```

Stage 4 的完整质量门禁是普通 pytest、显式 PostgreSQL integration pytest、OpenAPI 契约、Alembic head/drift、Ruff lint、Ruff format、mypy、lock check 和 diff check。SQLite 不作为 PostgreSQL integration 行为的替代品。

## Stage 4 最终验证顺序

下面的流程只操作可丢弃的 `postgres-test`。重建该服务可获得空 `tmpfs`；不要启动、重建或停止 `postgres-dev`，也不要删除开发 named volume。Stage 4 没有新增数据库结构，因此这里只升级到现有 head 并检查 metadata drift，不执行新的迁移往返。

1. 在没有数据库连接的情况下运行普通 pytest、warnings、Ruff、mypy、lock 和 diff 检查。
2. 运行 `docker version` 和 `docker compose config --quiet`。
3. 配置同一个专用测试端口和 `STMS_TEST_DATABASE_URL`，再只重建并启动 `postgres-test`。
4. 使用 `validate_migration_test_target` 验证驱动、本地主机、数据库、用户和配置端口。
5. 对空测试库执行 `alembic upgrade head`。
6. 运行 `alembic current`、`alembic heads` 和 `alembic check`，确认唯一 head 为 `9f3b2d6e8a41` 且没有 metadata drift。
7. 运行全部 integration 测试，包含真实注册、登录、无效 Token、当前用户读写、顺序重复和确定性双 Session 并发冲突。
8. 只运行 `docker compose stop postgres-test`；不要执行 `down --volumes`。

## 当前项目结构

```text
FastAPI-STMS/
|-- app/
|   |-- api/
|   |   |-- v1/
|   |   |   |-- endpoints/
|   |   |   |   |-- __init__.py
|   |   |   |   |-- auth.py
|   |   |   |   `-- users.py
|   |   |   |-- __init__.py
|   |   |   `-- router.py
|   |   |-- __init__.py
|   |   |-- health.py
|   |   `-- router.py
|   |-- core/
|   |   |-- config.py
|   |   |-- email_normalization.py
|   |   |-- exceptions.py
|   |   |-- security.py
|   |   `-- tokens.py
|   |-- db/
|   |   |-- __init__.py
|   |   |-- base.py
|   |   |-- probe.py
|   |   `-- session.py
|   |-- models/
|   |   `-- user.py
|   |-- repositories/
|   |   `-- users.py
|   |-- schemas/
|   |   |-- auth.py
|   |   |-- health.py
|   |   `-- user.py
|   |-- services/
|   |   |-- authentication.py
|   |   |-- current_user.py
|   |   `-- registration.py
|   |-- __init__.py
|   `-- main.py
|-- alembic/
|   |-- versions/
|   |   |-- 20260825_0001_stage_2_baseline.py
|   |   |-- 20260826_0002_create_users_table.py
|   |   `-- 20260826_0003_add_user_email_unique_constraint.py
|   |-- env.py
|   `-- script.py.mako
|-- docs/
|   |-- architecture.md
|   |-- requirements.md
|   `-- roadmap.md
|-- tests/
|   |-- __init__.py
|   |-- conftest.py
|   |-- integration/
|   |   |-- __init__.py
|   |   |-- conftest.py
|   |   |-- test_authentication.py
|   |   |-- test_database_connection.py
|   |   |-- test_migrations.py
|   |   |-- test_registration.py
|   |   |-- test_registration_conflict.py
|   |   |-- test_readiness.py
|   |   |-- test_user_email_uniqueness.py
|   |   |-- test_user_migration.py
|   |   `-- test_user_repository.py
|   |-- test_access_tokens.py
|   |-- test_alembic_config.py
|   |-- test_auth_dependencies.py
|   |-- test_auth_schemas.py
|   |-- test_authentication_service.py
|   |-- test_config.py
|   |-- test_current_user_api.py
|   |-- test_current_user_service.py
|   |-- test_db_session.py
|   |-- test_health.py
|   |-- test_integration_database_safety.py
|   |-- test_login_api.py
|   |-- test_registration_api.py
|   |-- test_registration_service.py
|   |-- test_security.py
|   `-- test_main.py
|-- alembic.ini
|-- compose.yaml
|-- .env.example
|-- .gitignore
|-- .python-version
|-- AGENTS.md
|-- CODEX_FASTAPI_LEARNING_TASK_SYSTEM.md
|-- pyproject.toml
|-- README.md
`-- uv.lock
```

该结构只列出当前版本中实际存在并与项目使用有关的文件和目录。

## 当前范围与下一阶段

Stage 4 已实现版本化注册和登录、短期 Access Token、严格 Bearer 校验、当前用户读取、邮箱更新、同步 Session/事务边界，以及真实 PostgreSQL HTTP 与并发测试。当前 API 是后续项目、任务和 Agent 能力复用的认证基础，不代表这些后续能力已经完成。

Stage 5 的 Refresh Token、轮换、退出登录和密码修改保留为非阻塞的延后认证增强轨道。StudyFlow Agent MVP 的下一条关键路径从 Stage 6 项目领域开始；进入前必须先验收 Stage 4 并审查 Stage 6 的详细任务契约，不得直接创建项目、任务或 Agent 代码。
