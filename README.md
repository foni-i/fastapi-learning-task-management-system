# FastAPI STMS

FastAPI STMS 是 StudyFlow Agent 的分阶段后端学习项目。当前已完成 Stage 1～4 的应用、数据库和认证基础，Stage 6～7 的用户私有 Project/Task API，Stage 8～10 的有界单Agent工作流，Stage 11 的安全RAG与离线评估，以及 Stage 12 的可复现工程交付。所有已实现链路使用 FastAPI、Pydantic 2、SQLAlchemy 2 同步 Session、PostgreSQL、Alembic、Argon2id 和固定 HS256 JWT，并有真实 PostgreSQL 端到端测试。

Stage 5 已完成并经 owner 验收：Refresh Token 摘要存储、原子轮换、双令牌登录、HTTP refresh、单凭据退出，以及密码修改与全部刷新凭据原子吊销。当前仅进行 [Stage 5 checkpoint 提交准备](docs/tasks/stage-5-checkpoint-preparation.md)，不表示已经 commit/push 或通过本次远程 CI。管理员、密码找回、grounded claim事实核验、外部Tracing供应商、MCP或多Agent尚未实现。Agent与Embedding普通测试只使用离线合成Provider；真实外部模型调用必须单独授权，检索citation只表示来源而不保证内容事实为真。

集中式的失败模式、安全保证/非保证、改进优先级和成本公式见
[`docs/security-and-limitations.md`](docs/security-and-limitations.md)。

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

现有配置项及边界如下。标为“敏感”的值只能存在于当前 shell、部署密钥存储或未提交的
`.env`，不能进入 Git、日志或截图：

| 变量 | 用途与适用路径 | 是否敏感/必需 |
| --- | --- | --- |
| `STMS_ENV` | `development`、`test` 或 `production` 运行标识 | 非敏感；可选 |
| `STMS_DEBUG` | FastAPI 调试模式 | 非敏感；可选，生产应为 `false` |
| `STMS_API_DOCS_ENABLED` | 控制 `/docs`、`/redoc`、`/openapi.json` | 非敏感；可选 |
| `STMS_DATABASE_URL` | 本机 uv、应用与 Alembic 的同步 Psycopg URL | 敏感；数据库路径必需 |
| `STMS_ACCESS_TOKEN_SECRET` | HS256 Access Token 签名，至少 32 字符 | **敏感**；登录和 Bearer API 必需 |
| `STMS_ACCESS_TOKEN_TTL_MINUTES` | Token 有效期，1～60 分钟 | 非敏感；默认 15 |
| `STMS_ACCESS_TOKEN_ISSUER` / `STMS_ACCESS_TOKEN_AUDIENCE` | Token 签发者与受众校验 | 非敏感；有安全默认值 |
| `STMS_MODEL_PROVIDER` / `STMS_MODEL_NAME` | 真实 Agent Provider 与模型 ID | Provider 调用时必需 |
| `STMS_EMBEDDING_MODEL` / `STMS_EMBEDDING_TIMEOUT_SECONDS` | 1536 维文档索引模型与 0.1～120 秒超时 | 显式索引时必需 |
| `STMS_MODEL_API_KEY` | 模型和 Embedding Provider 凭据 | **敏感**；真实 Provider 调用时必需 |
| `STMS_APP_PORT` | Compose 暴露到宿主机的应用端口 | 非敏感；Compose 默认 8000 |
| `STMS_POSTGRES_DEV_*` | Compose 开发库名称、用户、密码和宿主端口 | 密码敏感；仅本机 Compose |
| `STMS_POSTGRES_TEST_*` | Compose 专用测试库名称、用户、密码和宿主端口 | 密码敏感；仅 integration |
| `STMS_TEST_DATABASE_URL` | integration 测试显式 opt-in 的专用测试库 URL | **敏感**；只允许指向 `postgres-test` |

基础 `/health/live`、OpenAPI 和普通离线测试不需要数据库、JWT secret 或 Provider key；
readiness 与业务 API 需要数据库，登录/受保护 API 需要 JWT secret，显式索引与 Agent
模型执行才需要 Provider 配置。普通 pytest 和 Stage 11 离线评估不得调用真实 Provider。

## 一键启动应用与开发数据库

下面的单条命令构建锁定依赖的应用镜像，只启动 `app` 及其必要的
`postgres-dev`，等待数据库和应用都通过健康检查；它不会启动
`postgres-test`：

```powershell
docker compose up -d --build --wait app
```

容器先运行 `alembic upgrade head`，成功后才以非 root 用户启动监听
`0.0.0.0:8000` 的 Uvicorn。应用通过 Compose 内部网络连接
`postgres-dev:5432`，不依赖宿主机数据库端口；Compose readiness 请求
`/health/ready`，因此同时验证应用进程和数据库连接。

默认可访问 `http://127.0.0.1:8000/health/live`、
`http://127.0.0.1:8000/health/ready` 和
`http://127.0.0.1:8000/openapi.json`。可通过 `STMS_APP_PORT` 改变宿主端口。
应用和两个 PostgreSQL 服务的宿主发布地址固定为 `127.0.0.1`，默认仅供本机访问。
`STMS_APP_PORT`、`STMS_POSTGRES_DEV_PORT`、`STMS_POSTGRES_TEST_PORT` 只改变端口，
不改变绑定地址。容器内 Uvicorn 仍监听 `0.0.0.0:8000`，应用通过内部 DNS
`postgres-dev:5432` 访问数据库。远程访问需要操作者显式传入额外的 `-f` Compose
override，并自行配置防火墙、TLS 和非示例凭据；默认配置不提供远程发布开关。
已有容器必须在端口配置重新应用后才使用新绑定，仅 `docker compose start` 不会更新它。
Compose 不提供任何 JWT 签名默认值，只会透传当前 shell 或未提交 `.env` 中显式设置的
`STMS_ACCESS_TOKEN_SECRET`。未配置时，基础启动、health、OpenAPI 和不需要认证的路径
仍可使用，但 Token 签发与校验会安全失败；需要登录时必须提供至少 32 字符的随机值。
Provider key 同样保持可选，基础启动和健康检查不会调用外部模型。

验收或本地使用结束后只停止应用和开发数据库；此命令保留容器与
`fastapi-stms-postgres-dev-data` named volume：

```powershell
docker compose stop app postgres-dev
```

不要使用 `docker compose down -v`，也不要为了运行应用而启动或修改
`postgres-test`。

## SQLAlchemy 数据库基础设施

项目使用 SQLAlchemy 2 同步 API，各组件职责如下：

- `Base`（`app/db/base.py`）提供共享 declarative metadata；Stage 3 的 `User` ORM 映射到 `users` 表。
- `Engine`（`app/db/session.py`）管理连接池和数据库方言。它按进程缓存、同步运行，并配置有限的连接超时；创建 Engine 不会立即连接，首次 `connect()` 或 Session 执行 SQL 时才访问 PostgreSQL。
- `sessionmaker` 绑定上述 Engine，用于创建短生命周期的同步 `Session`。`get_session()` 每个请求 yield 一个 Session，并在 `finally` 中关闭；注册和当前用户写 Service 拥有 `commit`/`rollback`，Repository 只查询、`add`、修改和 `flush`。登录及当前用户读取不提交事务。
- readiness 探针使用短生命周期 `Connection` 执行 `SELECT 1`，通过上下文管理器在成功或查询异常时归还连接池。它不创建 Session，也不修改数据。

## PostgreSQL 开发与测试环境

`compose.yaml` 使用固定镜像 `pgvector/pgvector:0.8.6-pg17-bookworm`，在 PostgreSQL 17 上提供固定 pgvector 扩展版本，并把两个数据库放在同一个明确命名的 `fastapi-stms` Compose 项目中：

| 用途 | Service | 主机端口 | 数据库 | 用户 | 数据策略 |
| --- | --- | --- | --- | --- | --- |
| 开发 | `postgres-dev` | `5432` | `stms` | `stms_dev` | named volume，停止/重建容器后保留 |
| 测试 | `postgres-test` | 默认 `5433` | `stms_test` | `stms_test` | `tmpfs`，容器移除后丢弃 |

以上宿主端口均绑定 `127.0.0.1`；容器之间的访问通过 Compose 内部网络进行。

`.env.example` 中的数据库账号和密码只用于本机开发演示，不是生产凭据。若复制为 `.env` 并修改，绝对不要提交 `.env`。

启动并检查两个数据库：

```powershell
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw "Compose configuration check failed" }
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

uv run python -c "import os; from tests.integration.conftest import validate_migration_test_target; validate_migration_test_target(os.environ.get('STMS_DATABASE_URL')); print('validated dedicated migration test target')"
if ($LASTEXITCODE -ne 0) { throw "Migration test target validation failed" }
uv run alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Migration upgrade failed" }
uv run alembic current
if ($LASTEXITCODE -ne 0) { throw "Migration current failed" }
uv run alembic downgrade base
if ($LASTEXITCODE -ne 0) { throw "Migration downgrade failed" }
uv run alembic current
if ($LASTEXITCODE -ne 0) { throw "Migration current failed" }
uv run alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Migration upgrade failed" }
uv run alembic current
if ($LASTEXITCODE -ne 0) { throw "Migration current failed" }
uv run alembic check
if ($LASTEXITCODE -ne 0) { throw "Migration check failed" }
Remove-Item Env:STMS_DATABASE_URL -ErrorAction SilentlyContinue
uv run pytest -m integration tests/integration/test_migrations.py
```

`downgrade` 可能删除或变更 schema，绝不能在开发或生产数据库上把它当作普通检查运行。
将整个代码块作为一个 PowerShell 脚本执行；原生命令失败不会自动抛出异常，因此必须
立即检查 `$LASTEXITCODE` 并 `throw`，验证失败后不得继续执行剩余命令。
验证必须先于任何 Alembic 操作，并严格验证驱动、本地主机、数据库 `stms_test`、
用户 `stms_test`，以及 URL 与 `STMS_POSTGRES_TEST_PORT` 选择的主机端口一致；
未显式配置时默认端口为 `5433`。操作期间不得更换已验证的 URL；更换后必须重新验证。
测试 fixture 在每次自动 downgrade 前也执行相同校验。

Bash 用户在专用测试服务已启动后，可使用下面的独立子 shell。先在本机安全设置
`STMS_TEST_DATABASE_URL`，不要打印连接串，也不要开启 `set -x`。子 shell 任一步失败
立即退出，不会继续迁移；父 shell 的环境变量不受影响：

```bash
(
  set -eu
  docker compose config --quiet || exit 1
  : "${STMS_TEST_DATABASE_URL:?Set the dedicated test URL locally first}"
  export STMS_DATABASE_URL="$STMS_TEST_DATABASE_URL"
  uv run python -c "import os; from tests.integration.conftest import validate_migration_test_target; validate_migration_test_target(os.environ.get('STMS_DATABASE_URL')); print('validated dedicated migration test target')" || exit 1
  uv run alembic upgrade head || exit 1
  uv run alembic current || exit 1
  uv run alembic downgrade base || exit 1
  uv run alembic current || exit 1
  uv run alembic upgrade head || exit 1
  uv run alembic current || exit 1
  uv run alembic check || exit 1
)
```
Alembic 命令需要临时把 `STMS_DATABASE_URL` 指向已验证的专用测试库；进入
integration pytest 前必须移除该变量，只保留 `STMS_TEST_DATABASE_URL`，否则测试
安全门会按设计拒绝把测试目标同时当成开发数据库。

验证后只停止测试服务；此命令不会停止 `postgres-dev`，也不会删除或修改开发 named volume：

```powershell
docker compose stop postgres-test
Remove-Item Env:STMS_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:STMS_TEST_DATABASE_URL -ErrorAction SilentlyContinue
```

## 使用本机 uv 启动应用

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

## API 快速演示

完整的默认无 Provider 演示步骤、录制分镜和脱敏结果快照见
[`docs/demo/README.md`](docs/demo/README.md)。本节保留唯一的 HTTP payload 示例，演示
文档直接引用这里，避免维护第二套接口契约。

以下流程以 PowerShell 7、运行在 `http://127.0.0.1:8000` 的本地服务和完全合成数据为例。
先在未提交的 `.env` 或当前 shell 中配置开发数据库和至少 32 字符的 JWT secret。
示例把返回的 Token 保存在当前 PowerShell 变量中；不要打印、复制到文档或提交到 Git。

```powershell
$api = "http://127.0.0.1:8000"
Invoke-RestMethod -Uri "$api/health/live"

$credentials = @{
    email = "demo.learner@example.com"
    password = "<local-demo-password-at-least-12-characters>"
}
Invoke-RestMethod -Method Post -Uri "$api/api/v1/auth/register" `
    -ContentType "application/json" -Body ($credentials | ConvertTo-Json)
$login = Invoke-RestMethod -Method Post -Uri "$api/api/v1/auth/login" `
    -ContentType "application/json" -Body ($credentials | ConvertTo-Json)
$headers = @{ Authorization = "Bearer $($login.access_token)" }
Invoke-RestMethod -Uri "$api/api/v1/users/me" -Headers $headers
```

注册成功为 HTTP 201，登录和当前用户为 HTTP 200。公开用户响应只含
`id`、`email`、`created_at`、`updated_at`。继续创建一个 Project 和所属 Task：

```powershell
$projectBody = @{
    name = "Synthetic retrieval study"
    description = "Local documentation walkthrough"
    start_date = "2026-09-07"
    target_date = "2026-10-05"
} | ConvertTo-Json
$project = Invoke-RestMethod -Method Post -Uri "$api/api/v1/projects" `
    -Headers $headers -ContentType "application/json" -Body $projectBody

$taskBody = @{
    project_id = $project.id
    title = "Review the synthetic syllabus"
    description = "Complete the first retrieval-practice session"
    planned_date = "2026-09-08"
    due_at = "2026-09-08T12:00:00Z"
    estimated_minutes = 25
    priority = "MEDIUM"
} | ConvertTo-Json
$task = Invoke-RestMethod -Method Post -Uri "$api/api/v1/tasks" `
    -Headers $headers -ContentType "application/json" -Body $taskBody
```

两个创建请求均返回 HTTP 201；公开响应不含 `user_id`。知识文档上传使用仓库自带的
[合成 syllabus](docs/demo/sample-syllabus.md)，上传只解析并保存文档，**不会自动调用**
Embedding Provider：

```powershell
$env:STMS_DEMO_ACCESS_TOKEN = $login.access_token
$document = curl.exe --fail-with-body `
    -H "Authorization: Bearer $env:STMS_DEMO_ACCESS_TOKEN" `
    -F "file=@docs/demo/sample-syllabus.md;type=text/markdown" `
    "$api/api/v1/knowledge/documents" | ConvertFrom-Json
```

上传成功为 HTTP 201。只有已经配置真实 Provider、API key 和 1536 维 embedding 模型时，
才显式索引；这一步可能产生外部调用和费用，不属于普通测试：

```powershell
$indexed = Invoke-RestMethod -Method Post `
    -Uri "$api/api/v1/knowledge/documents/$($document.id)/index" `
    -Headers $headers
```

Agent Run 同样只应在明确配置并授权真实模型调用后执行。请求不能携带 `user_id`、
Session、SQL、向量、Tool allowlist 或审批结果：

```powershell
$runBody = @{
    goal = @{
        objective = "Create a four-week study plan from my indexed material"
        constraints = @("Use sessions of at most 30 minutes")
    }
} | ConvertTo-Json -Depth 4
$snapshot = Invoke-RestMethod -Method Post -Uri "$api/api/v1/agent/runs" `
    -Headers $headers -ContentType "application/json" -Body $runBody

# Inspect the complete bounded proposal before deciding.
$preview = Invoke-RestMethod -Method Get `
    -Uri "$api/api/v1/agent/runs/$($snapshot.run.id)/approval-preview" `
    -Headers $headers

# Bind the decision to the exact revision and fingerprint just inspected.
$approvalBody = @{
    revision = $preview.revision
    proposal_fingerprint = $preview.proposal_fingerprint
    decision = "APPROVED"
} | ConvertTo-Json
$snapshot = Invoke-RestMethod -Method Post `
    -Uri "$api/api/v1/agent/runs/$($snapshot.run.id)/approval" `
    -Headers $headers -ContentType "application/json" -Body $approvalBody

# The response is text/event-stream; Last-Event-ID may resume after a known event.
Invoke-WebRequest -Uri "$api/api/v1/agent/runs/$($snapshot.run.id)/events" `
    -Headers $headers
```

Run 创建成功为 HTTP 201，读取、预览、审批和 SSE 为 HTTP 200。只有服务端返回非空、
状态为 pending 的 `approval` 时才能读取 preview 并构造审批请求；拒绝使用 `REJECTED`，请求修改使用
`REQUEST_CHANGES` 并额外提供最多 1000 字符的 `feedback`。完成演示后清除当前会话变量：

```powershell
Remove-Item Env:STMS_DEMO_ACCESS_TOKEN -ErrorAction SilentlyContinue
Remove-Variable login, headers, credentials -ErrorAction SilentlyContinue
```

## 认证与当前用户 API

当前健康检查与认证相关方法/路径如下，其他产品路由见后续章节：

| 方法 | 路径 | 结果 |
| --- | --- | --- |
| `GET` | `/health/live` | 应用进程存活状态 |
| `GET` | `/health/ready` | 数据库就绪状态 |
| `POST` | `/api/v1/auth/register` | 创建最小用户并返回公开字段 |
| `POST` | `/api/v1/auth/login` | 验证凭据并在摘要提交后交付 Access + Refresh Token |
| `POST` | `/api/v1/auth/refresh` | JSON body 刷新凭据原子轮换并交付双令牌 |
| `POST` | `/api/v1/auth/logout` | 只吊销所提交的 Refresh Token，幂等空 204 |
| `GET` | `/api/v1/users/me` | 返回已认证用户的公开字段 |
| `PATCH` | `/api/v1/users/me` | 只更新已认证用户的规范化邮箱 |
| `POST` | `/api/v1/users/me/change-password` | 当前密码验证后修改密码并吊销该用户全部刷新凭据，空 204 |

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

### 登录、Access Token 与 Refresh Token

以下为 Task 5.4 已实现契约；离线与真实 HTTP/PostgreSQL 验证通过，详见[任务记录](docs/tasks/stage-5-4-refresh-http.md)。

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
  "token_type": "bearer",
  "refresh_token": "<refresh-token>",
  "refresh_expires_at": "2026-09-25T00:00:00Z"
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
- Access Token 不存入数据库。当前没有主动撤销或 denylist，只能依靠短有效期到期；刷新、退出或密码修改后旧 Access Token 仍按原期限有效。

`POST /api/v1/auth/logout` 只接受 JSON `{ "refresh_token": "<refresh-token>" }`
（占位符需替换为客户端持有的实际凭据，不要打印）。无需有效 Access Token；成功、
不存在或已经撤销均返回无响应体 204，无效输入 422，数据库故障 503，均禁止缓存。
只撤销所提交凭据，不影响其他会话或已轮换的后继。客户端应串行处理刷新与退出，
提交最新 Refresh Token 并清除本地凭据；退出不立即撤销 Access JWT。
详见 [Task 5.5 契约与验证](docs/tasks/stage-5-5-logout.md)。

`POST /api/v1/users/me/change-password` 要求有效 Bearer Access Token，JSON 仅为：

```json
{
  "current_password": "<current-password>",
  "new_password": "<new-password>"
}
```

以上只是占位符。当前密码限制 1～128 字符，新密码复用注册的 12～128 字符、非全空白
规则，不修改空格或大小写，新旧相同返回 422。用户 ID 只取认证身份；不接受目标用户。
成功返回无响应体 204，密码不匹配/身份无效 401，输入无效 422，基础设施故障 503；
响应禁止缓存且错误不回显密码。新密码摘要与该用户全部未撤销 Refresh Token 的吊销
在同一事务内提交，提交前失败一起回滚；不会影响其他用户，也不会自动交付新令牌。
客户端应清除旧凭据并用新密码重新登录。Access JWT 仍按原 TTL 有效。
用户优先行锁使并发旧密码登录/旧凭据刷新无法漏过改密吊销；提交或响应确认丢失时
新密码可能已生效，不能保证用旧密码重试成功。详见 [Task 5.6](docs/tasks/stage-5-6-password-change.md)。

Stage 5 的安全保证、测试证据与可复现命令集中在
[Task 5.7 验收记录](docs/tasks/stage-5-7-auth-security-acceptance.md)。注意：

- 持有同一用户行锁超过等待上限，会使 login/refresh/logout/change-password 返回固定
  503；释放锁后可以重新请求，但这不是完整的请求超时或限流策略。
- 503 不保证“数据库没有写入”：提交成功后响应/确认丢失，login 可能留下未交付凭据，
  refresh 的旧凭据可能已消费，改密的新密码可能已生效。logout 可按同一凭据幂等重试；
  refresh 通常需重新登录，改密应尝试新密码登录。
- 未交付的刷新凭据没有去重/自动清理任务，记录保留；凭据到期后不能刷新，改密也能
  吊销该用户全部既有刷新凭据。没有即时 Access JWT 吊销或生产安全认证保证。

`POST /api/v1/auth/refresh` 只接受 JSON body 中的 `refresh_token`，必须是签发的 43 字符
URL-safe 凭据，不接受 user_id，不从 Cookie、query 或 Authorization 获取刷新凭据。
刷新不要求有效 Access Token；新身份始终来自旧 Refresh Token 记录。使用 JSON body
意味着调用方负责安全保管凭据；正式部署需要 HTTPS，不把令牌放进 URL/日志或浏览器
localStorage。本阶段不设置 Cookie，不宣称提供浏览器持久登录或 CSRF Cookie 方案。

```powershell
$refreshBody = @{ refresh_token = $login.refresh_token } | ConvertTo-Json
$login = Invoke-RestMethod -Method Post `
    -Uri http://127.0.0.1:8000/api/v1/auth/refresh `
    -ContentType "application/json" -Body $refreshBody
# 不输出 $login 或 $refreshBody；成功后只使用最新凭据。
```

成功响应仍为上述四字段；刷新凭据仅存 SHA-256 摘要，期限为每次轮换起 7 天。
旧凭据提交后不可重用；格式合法但不存在/过期/撤销/重放统一 401，schema/JSON 无效
返回无原始输入的 422，签名/数据库故障返回固定 503。login/refresh 的 200/401/422/503
响应包含 `Cache-Control: no-store`、`Pragma: no-cache`。字段新增会影响严格两字段客户端。
撤销、替代摘要插入和 JWT 签发在单次提交边界内；提交前失败回滚，不返回部分凭据。
提交确认或 HTTP 响应丢失仍可能需要重新登录，不能盲目自动重试旧凭据。

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

## Stage 6 Project API

Stage 6 已实现经过 Bearer 认证的用户私有 Project 核心。正式路由为：

| 方法 | 路径 | 结果 |
| --- | --- | --- |
| `POST` | `/api/v1/projects` | 创建当前用户的 Project，返回 201 |
| `GET` | `/api/v1/projects` | 返回当前用户的固定分页列表 |
| `GET` | `/api/v1/projects/{project_id}` | 返回一个当前用户拥有的 Project |
| `PATCH` | `/api/v1/projects/{project_id}` | 严格部分更新非归档 Project |
| `POST` | `/api/v1/projects/{project_id}/archive` | 幂等归档 Project |

请求必须携带 `Authorization: Bearer <access-token>`，其中 Token 仅使用本地
登录接口签发的值；不要把真实 Token 写入源码、文档或日志。`user_id` 只来自认证
上下文，客户端不能提交或覆盖它，公开响应也不会返回它。`PublicProject` 的字段
白名单严格为 `id`、`name`、`description`、`start_date`、`target_date`、
`status`、`created_at` 和 `updated_at`。

创建请求只接受 `name`、`description`、`start_date` 和 `target_date`。`name`
去除首尾空白后必须为 1～200 字符；`description` 去除首尾空白后最多 2000
字符，空白文本规范化为 `null`。日期可为空；同时存在时 `target_date` 不得早于
`start_date`。初始状态固定为 `NOT_STARTED`，其余公开状态为
`IN_PROGRESS`、`COMPLETED` 和 `ARCHIVED`。

更新请求可包含 `name`、`description`、`start_date`、`target_date` 和非归档
状态。它区分字段未提供与显式 `null`；后者可清空三个可选字段。普通 PATCH
不能直接设置 `ARCHIVED`，归档必须使用专用动作。实际变化推进 `updated_at`，
无有效变化的请求保持幂等且不写数据库。归档动作第一次持久化状态，重复调用返回
相同公开结果且不再次更新时间。归档后普通 PATCH 返回 HTTP 409：
`{"detail":"Archived project cannot be modified"}`。

列表参数固定为 `page`（默认 1）、`page_size`（默认 20，最大 100）和
`include_archived`（默认 `false`）。顺序固定为 `created_at DESC, id DESC`；
响应包含 `items`、`page`、`page_size`、`total` 和 `pages`。默认不返回归档
记录，显式 `include_archived=true` 才包含它们；当前没有公开 sort、search 或
`user_id` 参数。

所有详情、更新和归档查询同时限定 Project ID 与当前用户 UUID。资源不存在和访问
其他用户资源均返回相同的 HTTP 404：`{"detail":"Project does not exist"}`，
避免泄露所有者和记录是否存在。Router 只处理 HTTP；Service 拥有写操作的
`commit`/`rollback`；Repository 只执行带所有权条件的查询、`add`、字段变更和
`flush`；请求依赖创建并关闭同步 SQLAlchemy Session。PostgreSQL 中命名的主键、
外键、非空白名称、状态、日期顺序检查约束和所有者索引是绕过 HTTP 写入时的最终
完整性防线。

真实验收只使用 `postgres-test`。默认宿主机端口为 5433；Windows 排除该端口时，
可在当前 PowerShell 进程中把 `STMS_POSTGRES_TEST_PORT` 设置为 15433，并让
`STMS_TEST_DATABASE_URL` 使用同一端口。执行迁移前必须通过现有测试数据库安全门，
且不得输出完整 URL 或密码、操作 `postgres-dev`、删除 volume，或把 SQLite 当作
PostgreSQL 行为的替代。

Stage 6验收时仍没有 Project DELETE/restore；Project 的移除语义保持幂等归档。
Task能力随后在Stage 7中独立实现，Agent能力则在Stage 8～10中逐步加入。

## Stage 7 Task API

Stage 7 已实现经过 Bearer 认证、严格按当前用户隔离的 Task API。正式路由为：

| 方法 | 路径 | 结果 |
| --- | --- | --- |
| `POST` | `/api/v1/tasks` | 在当前用户拥有的 Project 下创建 Task，返回 201 |
| `GET` | `/api/v1/tasks` | 分页、筛选并稳定排序当前用户的 Task |
| `GET` | `/api/v1/tasks/{task_id}` | 返回当前用户拥有的一个 Task |
| `PATCH` | `/api/v1/tasks/{task_id}` | 严格部分更新字段或重开已完成 Task |
| `POST` | `/api/v1/tasks/{task_id}/complete` | 幂等完成 Task |
| `DELETE` | `/api/v1/tasks/{task_id}` | 永久删除Task，返回无响应体的204 |

`PublicTask` 只返回 `id`、`project_id`、`title`、`description`、`status`、
`priority`、`planned_date`、`due_at`、`estimated_minutes`、`completed_at`、
`created_at` 和 `updated_at`。`user_id` 只来自认证上下文，不接受客户端输入，
也不进入公开响应。

创建请求只接受 `project_id`、`title`、`description`、`planned_date`、`due_at`、
`estimated_minutes` 和 `priority`。标题去除首尾空白后为1～300字符；描述去除
首尾空白后最多5000字符，纯空白规范化为 `null`；预计分钟数为1～1440。
状态初始固定为 `TODO`，优先级默认为 `MEDIUM`。

PATCH 可更新 `title`、`description`、`planned_date`、`due_at`、
`estimated_minutes`、`priority` 和非完成状态。它严格区分“字段未提供”和
“显式 `null`”：后者可清空描述、计划日期、截止时间和预计分钟数。客户端不能
提交 `id`、`user_id`、`project_id`、`completed_at` 或时间戳，也不能通过 PATCH
直接设置 `COMPLETED`。

状态为 `TODO`、`IN_PROGRESS`、`COMPLETED` 和 `CANCELLED`；优先级为 `LOW`、
`MEDIUM`、`HIGH` 和 `URGENT`。专用完成动作从任意非完成状态进入 `COMPLETED`，
由服务端用同一个UTC时刻填写 `completed_at` 和 `updated_at`。重复完成不写入、
不提交，也不改变时间戳。已完成Task可通过PATCH重开为 `TODO`、`IN_PROGRESS`
或 `CANCELLED`，并原子清除 `completed_at`。普通非完成状态转换采用roadmap中的
显式允许集合；不允许的转换返回安全422。

同时存在 `planned_date` 和 `due_at` 时，截止时间不得早于计划日期的UTC起点。
PATCH使用数据库现值和本次输入组成最终状态后再校验，因此不能通过分两次请求绕过
日期不变量。相同规范化值的PATCH是幂等操作，不推进 `updated_at`，也不打开写事务。

列表参数如下：

- `page`：默认1，最小1；
- `page_size`：默认20，范围1～100；
- `project_id`、`status`、`priority`；
- `planned_from`、`planned_to`；
- `due_from`、`due_to`，必须是带时区时间；
- `overdue`；
- `title`，执行大小写不敏感的文字包含搜索，`%` 和 `_` 不作为通配符；
- `sort_by`：仅允许 `created_at`、`updated_at`、`due_at`、`planned_date`、`title`；
- `sort_direction`：仅允许 `asc` 或 `desc`。

分页响应严格包含 `items`、`page`、`page_size`、`total` 和 `pages`。排序始终用
`id` 作为同方向的确定性次级键；`due_at` 和 `planned_date` 排序时空值置后。
“逾期”精确定义为 `due_at < 当前UTC时间` 且状态不是 `COMPLETED` 或
`CANCELLED`。

所有详情、列表、更新、完成和删除查询都包含认证用户UUID。访问其他用户Task与随机
不存在的Task返回完全相同的HTTP 404：`{"detail":"Task does not exist"}`。
创建Task前还会确认目标Project属于同一用户；PostgreSQL复合外键
`fk_tasks_project_id_user_id_projects` 是绕过应用层写入时的最终所有权防线。

Task删除是永久硬删除，成功只返回HTTP 204且没有响应体。Stage 7没有
`deleted_at`、软删除、archive、restore或批量删除语义，也不会删除所属Project。

Task请求继续遵循同步分层：Router只处理HTTP和认证依赖；Schema限制输入输出；
Service拥有业务规则及写操作的 `commit`/`rollback`；Repository执行带所有权条件的
查询、`add`、字段修改、`delete` 和 `flush`，从不自行提交或回滚；请求依赖负责关闭
同步Session。数据库中的命名主键、外键、状态、优先级、分钟数、日期和完成时间检查
约束是直接写入时的最终完整性防线。

Stage 7验收时没有标签、学习记录、重复任务、提醒或协作；Agent能力在后续
Stage 8～10中实现，RAG仍属于Stage 11范围。

### Stage 7真实PostgreSQL验证

真实验收只使用可丢弃的 `postgres-test`。默认宿主机端口为5433；Windows排除该
端口时，可在当前PowerShell进程中把 `STMS_POSTGRES_TEST_PORT` 设为15433，并让
`STMS_TEST_DATABASE_URL` 使用相同端口。不得输出完整URL或密码，也不得操作
`postgres-dev` 或删除任何volume。

```powershell
uv run pytest tests/test_task_model.py tests/test_task_schemas.py tests/test_task_repository.py tests/test_task_service.py tests/test_task_api.py
uv run pytest
uv run pytest -W always -q

# 先通过tests/integration/conftest.py中的专用测试库安全门并迁移到head。
uv run pytest -m integration tests/integration/test_tasks.py
uv run pytest -m integration tests/integration

uv run alembic current
uv run alembic heads
uv run alembic check
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

Stage 7迁移往返使用唯一Task head `6e2f9a4c1b73`：从空的专用测试库升级到head，
降级到Project边界 `4d8c7a1b2e90`（Users与Projects保留、Tasks移除），再升级到
head并运行 `alembic check`。这些命令不得指向开发数据库。

## Stage 8～10 Agent工作流

当前Agent实现是一个有界、可恢复、需要明确人工审批的单Agent工作流。普通测试使用
脚本化离线Provider，不会访问外部模型。模型只能产生严格结构化计划并选择固定
allowlist中的Tool；可信用户身份来自Bearer认证上下文，Tool不能接收或覆盖
`user_id`，也不能直接访问Session或Repository。调用方向固定为：

```text
HTTP Agent API -> LangGraph node -> Agent Tool -> Domain Service
               -> owner-scoped Repository -> PostgreSQL
```

公开Agent路由为：

| 方法 | 路径 | 结果 |
| --- | --- | --- |
| `POST` | `/api/v1/agent/runs` | 创建服务端Thread/Run并执行到审批中断，返回201 |
| `GET` | `/api/v1/agent/runs/{run_id}` | 读取当前用户拥有的安全Run快照 |
| `GET` | `/api/v1/agent/runs/{run_id}/approval-preview` | 读取当前待审批 proposal 的完整、有界、类型化公开预览 |
| `POST` | `/api/v1/agent/runs/{run_id}/approval` | 提交批准、拒绝或修改请求并恢复Run |
| `GET` | `/api/v1/agent/runs/{run_id}/events` | 获取有序、可恢复的安全SSE进度事件 |

客户端不能提供 `thread_id`、`run_id` 或 `user_id`。Thread ID是LangGraph
`configurable.thread_id`使用的稳定产品身份，Run ID只标识一次产品执行，不作为
Checkpoint ID。跨用户读取和不存在的Run统一返回安全404；审批必须与当前Run、
revision和proposal fingerprint严格匹配。相同decision及规范化feedback的重试会恢复
未完成的checkpoint，或在已完成时返回同一安全Run快照；不同revision、fingerprint、
decision或feedback仍返回409。

审批 preview 只从当前已验证的 durable interrupt 读取，并在同一 run-scoped recovery
lock 下核对产品 approval 与 checkpoint。它返回公开 planning result 和最多3个按原顺序的
类型化 Task write action，保留 update 字段的 omitted/null 区别；canonical JSON 最多
65,536 UTF-8 bytes，超限、无法无损重建 fingerprint 或状态不一致时失败关闭而不截断。
Preview 不包含身份、凭据、Prompt、文档正文、hidden reasoning、checkpoint ID 或 Tool result。

高影响的 `batch_create_tasks` 和 `delete_task` 由代码固定分类，必须同时满足写Tool
开关、已验证提案、匹配的持久化审批以及数据库幂等claim。产品审计只持久化有界的
Run、Approval和Tool执行摘要，不保存Tool参数、完整Prompt、模型原始响应或隐藏推理。
LangGraph官方PostgreSQL Checkpoint只负责恢复图状态，不能替代产品审计记录。
审批恢复使用由完整Run UUID稳定映射的PostgreSQL transaction advisory lock；锁由独立
短生命周期连接持有，覆盖获锁后重读、公开checkpoint检查、resume/continue/reconcile
和最终产品提交。进程终止会随连接关闭释放锁。未知checkpoint或基础设施结果保留
`RUNNING`以供相同请求重试并返回固定503，不会猜测完成状态或自动重放`UNKNOWN` Tool。

SSE事件版本为 `agent-event.v1`，公开run/node状态、Tool开始与安全结果摘要、审批需求、
安全指标、heartbeat、terminal result和safe error。事件ID在Run内稳定且单调，
`Last-Event-ID`只恢复其后的保留事件；无效或不属于该Run的cursor安全失败。数据库
读取在流式响应开始前已经完成并关闭请求Session，heartbeat不携带业务数据，一个
完成Run只产生一个terminal事件。

Stage 10真实验收仅使用可丢弃的 `postgres-test`。Windows不能绑定默认5433时，可在
当前进程使用 `STMS_POSTGRES_TEST_PORT=15433`，并让 `STMS_TEST_DATABASE_URL` 使用
同一端口。测试必须先通过专用数据库安全门，再从空库升级到唯一head
`c4d8a1f6e205`；最终验收降级到Stage 9边界 `6e2f9a4c1b73`，确认Stage 10产品表移除而
领域表保留，然后重新升级并运行 `alembic current`、`heads` 和 `check`。不得操作
`postgres-dev`、删除volume、输出完整数据库URL或使用SQLite代替PostgreSQL行为。

## Stage 11 文档解析、混合检索、安全 Grounding、Tracing 与离线评估

认证用户可通过 `POST /api/v1/knowledge/documents` 上传不超过5 MiB的 `.txt`、
`.md` 或未加密 `.pdf`，并通过 `GET /api/v1/knowledge/documents/{document_id}`
读取安全元数据。上传路由在 multipart 解析前以纯 ASGI 流式计数将整个请求体限制为
`5 MiB + 64 KiB`（文件预算加固定表单封装预算），且端点和Service仍独立执行5 MiB
文件上限。超限返回固定413；文件名或解析文本中的U+0000在Repository构造前返回固定
422，不回显输入。原始解析文本、SHA-256和 `user_id` 不会进入公开响应。

索引是单独的显式操作：

```text
POST /api/v1/knowledge/documents/{document_id}/index
  -> owner-scoped document lookup
  -> deterministic page-aware chunks (2000 chars, 200 overlap)
  -> configured synchronous EmbeddingProvider (1536 dimensions)
  -> atomic chunk replacement and INDEXED status
```

上传不会自动访问Embedding Provider。生产索引要求 `STMS_MODEL_PROVIDER=openai`、
`STMS_MODEL_API_KEY`、`STMS_EMBEDDING_MODEL` 和有界的
`STMS_EMBEDDING_TIMEOUT_SECONDS`。普通测试使用确定性Fake，不发起网络请求。
每份文档最多保存200个私有chunk；向量必须恰为1536维且所有值有限。数据库使用
复合owner外键、GIN `tsvector`索引和HNSW cosine索引，Repository不提交事务，
Service在替换成功后统一提交，任何失败都会回滚。

Task 11.2 已在专用的 PostgreSQL 17 `postgres-test` 上完成真实验收：`vector`
扩展版本为0.8.6，embedding列为 `vector(1536)`，生成式 `tsvector`、GIN索引与
HNSW cosine索引均由数据库目录确认；迁移降级到 `d7a1e4c9b320` 时documents表
保留且chunks表移除，随后可重新升级到唯一head。完整integration套件通过，且未
调用真实外部Embedding API。

Agent 只读 Tool `search_knowledge` 接受1～2000字符的 `query`、1～20的
`top_k`（默认10）以及最多20个可选 `document_ids`。`user_id` 始终来自可信运行
上下文，Tool参数不接受Session、SQL/tsquery、向量、运算符、权重或RRF配置。
Repository分别取得最多40个simple全文候选和40个cosine候选；两路都先应用chunk与
document的owner条件及可选document过滤，再排名。代码使用固定
`1 / (60 + rank)`贡献进行确定性RRF，相同chunk合并，融合分数相同时按chunk UUID
升序，最终最多20条。结果只公开稳定citation ID、安全来源、可选页码、ordinal、最多
500字符的excerpt以及有界排名证据；不会返回owner、完整文档、embedding、tsvector、
SQL或ORM对象。citation只标识检索来源，不保证内容事实为真。

`load_context` 使用可信运行身份，通过现有 `search_knowledge` Tool 以目标文本执行一次
有界检索。Agent state最多保存10条公开证据，Prompt中的Grounding区最多12,000字符，
并以版本化边界明确标记为不可信数据。检索文本不能改变身份、Tool allowlist、授权、
审批、系统规则或输出Schema。`study-plan.v2`允许每个计划步骤携带最多10个citation ID；
存在证据时每一步必须引用本次检索得到的ID，无证据时禁止伪造引用。引用验证发生在
审批之前，最终cited proposal的规范化内容进入approval fingerprint。公开结果和审批
载荷不会包含excerpt或完整文档。

Agent graph、node、Provider尝试和Tool执行边界支持注入同步 `TraceSink`。默认
No-op sink不保留任何内容；`agent-trace.v1`事件只允许run/thread关联ID、固定的
component/name、阶段、outcome、安全错误码、prompt版本、有界次数/token计数及非负
latency。Trace不包含goal、Prompt、文档、citation内容、Tool参数/结果、用户身份、
凭据、SQL、向量、异常文本或隐藏推理，也不会写入checkpoint、产品audit、SSE或
数据库。sink失败会被隔离，不能改变原工作流结果、重试次数、Tool调用或事务。

Stage 11保留 `stage11-eval.v1` / `stage11-baseline.v1` 作为历史工件；v1的
`fake_script` 会直接提供部分最终观察值且input未进入执行，因此不能作为当前准确率或
恢复能力证据。R6新增语义隔离的 `stage11-eval.v2` 30-case数据集和
`stage11-baseline.v2`。input实际进入goal/prompt或owner-scoped retrieval/grounding；
fake Provider接收生产 `ProviderRequest`，输出经过真实schema、graph、Tool、citation和
approval路径，expectation只在观察完成后参与判定。usage来自实际
`ProviderResponse`，latency来自注入clock调用。快速baseline无Docker、网络、密钥和
真实写；recovery/duplicate证据仅由guarded `postgres-test` 集成测试从公共checkpoint
状态及测试拥有的产品行生成，不进入快速gate。报告和baseline只保留安全ID、布尔值、
有界计数与聚合，不保存完整input、Prompt、文档、Tool参数/结果、Token或数据库URL。

当前没有独立的全设备退出接口、即时 Access JWT 吊销、神经reranker、grounded claim事实核验或
外部Tracing供应商，也没有公共搜索HTTP接口、MCP或多Agent。Task 11.8完成后Stage 11
结束，必须等待owner确认。

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

`.github/workflows/ci.yml` 包含两个独立 job：offline quality 和受保护的 PostgreSQL 17 +
pgvector integration。checkpoint commit `b05adae` 对应的
[GitHub Actions run 35097448961](https://github.com/foni-i/fastapi-learning-task-management-system/actions/runs/35097448961)
已实际运行且两个 job 均成功；这只证明该 commit 的 workflow，不代表当前未提交修改、
生产部署或真实 Provider 已通过。本地普通测试不因 CI 文件存在而访问网络或真实 Provider。

Stage 7 的完整质量门禁是普通 pytest、显式 PostgreSQL integration pytest、OpenAPI 契约、Alembic迁移往返和head/drift、Ruff lint、Ruff format、mypy、lock check与diff check。SQLite不作为PostgreSQL integration行为的替代品。

## Stage 7 最终验证顺序

下面的流程只操作可丢弃的 `postgres-test`。重建该服务可获得空 `tmpfs`；不要启动、重建或停止 `postgres-dev`，也不要删除开发named volume。

1. 在没有数据库连接的情况下运行普通 pytest、warnings、Ruff、mypy、lock 和 diff 检查。
2. 运行 `docker version` 和 `docker compose config --quiet`。
3. 配置同一个专用测试端口和 `STMS_TEST_DATABASE_URL`，再只重建并启动 `postgres-test`。
4. 使用 `validate_migration_test_target` 验证驱动、本地主机、数据库、用户和配置端口。
5. 对空测试库执行 `alembic upgrade head`。
6. 确认Task表、索引、外键和检查约束，再降级到Project边界 `4d8c7a1b2e90`；Users和Projects必须保留，Tasks必须移除。
7. 重新升级到唯一head `6e2f9a4c1b73`，运行 `alembic current`、`alembic heads` 和 `alembic check`。
8. 运行全部integration测试，覆盖注册、认证、当前用户、Project、Task、所有权、约束和Session清理。
9. 只运行 `docker compose stop postgres-test`；不要执行 `down --volumes`。

## 当前项目结构

```text
FastAPI-STMS/
|-- .github/workflows/ci.yml
|-- app/
|   |-- agent/          # 单 Agent 图、Tool、Grounding、Tracing、离线评估
|   |-- api/
|   |   |-- v1/
|   |   |   |-- endpoints/
|   |   |   |   |-- auth.py
|   |   |   |   |-- agent_runs.py
|   |   |   |   |-- knowledge_documents.py
|   |   |   |   |-- users.py
|   |   |   |   |-- projects.py
|   |   |   |   `-- tasks.py
|   |   |   `-- router.py
|   |   |-- health.py
|   |   `-- router.py
|   |-- core/            # 配置、认证、安全错误
|   |-- db/              # Base、Engine、Session、readiness probe
|   |-- models/          # 用户、项目、任务、Agent、知识文档 ORM
|   |-- repositories/    # 同步owner-scoped持久化
|   |-- schemas/         # 严格请求、查询和公开响应
|   |-- services/        # 业务规则与写事务边界
|   `-- main.py
|-- alembic/
|   |-- versions/        # 唯一迁移链，当前 head e3b7c2d9a410
|   |-- env.py
|   `-- script.py.mako
|-- evals/stage11/       # 合成数据集、baseline 与评估说明
|-- docs/
|   |-- architecture.md
|   |-- demo/sample-syllabus.md
|   |-- requirements.md
|   |-- roadmap.md
|   `-- tasks/           # Stage 12 独立任务说明
|-- tests/
|   |-- integration/     # 专用 postgres-test 行为与迁移验收
|   |-- external/        # 默认排除、需显式授权的 Provider smoke
|   |-- fakes/           # 无网络的确定性 Provider/Embedding/Trace fakes
|   `-- test_*.py        # 普通离线单元与契约测试
|-- .dockerignore
|-- Dockerfile
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

该结构是当前模块化单体的核心摘录；完整测试和模块列表以仓库目录为准。

## 当前范围与下一阶段

Stage 3、4、6和7已经形成注册、认证、当前用户、Project与Task的完整同步分层基础。Stage 7真实PostgreSQL验收证明全部Task HTTP操作、两用户隔离、状态机、稳定查询、命名约束、迁移往返和精确清理一致。

Stage 8提供可替换Provider、版本化Prompt、严格结构化结果、Service-backed Tool和有界循环；Stage 9使用八个明确节点组成单Agent LangGraph；Stage 10增加公开Run/审批API、官方PostgreSQL Checkpoint、产品审计记录、数据库幂等写入、高影响操作审批和安全SSE。普通测试不会访问外部模型。

外部Provider smoke test默认被`external_provider`标记排除。只有owner明确授权网络、凭据和可能产生的费用后，才可在仅包含合成提示的环境中显式设置`STMS_RUN_EXTERNAL_PROVIDER_SMOKE=1`并单独选择该marker；不得在普通CI中启用，也不得输出API Key或完整模型响应。

Stage 5 已实现 Refresh Token 存储、签发、轮换、HTTP 交付、单凭据退出，以及 Task 5.6 密码修改与全部刷新吊销。Task 5.7 汇总真实安全集成与文档证据，当前验收状态见 tasks 索引。Stage 11已经提供文档上传、私有解析、确定性分块、pgvector词法/向量RRF检索、受限于不可信数据边界和严格citation验证的Agent Grounding、不记录内容的有界Tracing，以及版本化离线评估、指标和安全门。当前尚未实现grounded claim事实核验或外部Tracing供应商。

Stage 12 已提供可复现的一键 Compose 应用启动、两段式 GitHub Actions、使用与架构文档、
无 Provider 演示以及安全/成本说明。checkpoint commit `b05adae` 的真实远端 CI
已通过；当前工作区中的文档事实更新不属于该远端 run。
MCP、多 Agent、Version 2 与生产云部署均不在当前范围。
