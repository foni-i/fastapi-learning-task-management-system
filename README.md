# FastAPI STMS

FastAPI STMS 是一个分阶段构建的学习项目。当前完成了阶段 1 应用骨架，以及阶段 2 的同步 SQLAlchemy 基础设施、Alembic 环境、相互隔离的 PostgreSQL 服务、专用集成测试框架、空 baseline migration 和数据库 readiness 检查。

目前尚未实现任何业务数据库表、认证、用户、项目、任务或 CI。不要把当前仓库当作完整的任务管理系统。

## 前置条件

- Git
- Windows PowerShell 5.1 或 PowerShell 7+
- 首次安装工具和依赖时可访问互联网
- `uv`；项目所需的 Python 3.14 由 `uv` 管理
- Docker Desktop，并启用 WSL 2 Linux 容器后端

运行 FastAPI 骨架本身不需要 PostgreSQL；运行阶段 2 数据库环境需要 Docker Desktop。

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
- 当前示例支持运行环境、调试模式、API 文档开关，以及本地开发/测试 PostgreSQL 服务配置。
- 数据库 URL 和密码只能来自环境变量或未提交的 `.env`；不要放入 README、`alembic.ini`、源码、日志或测试输出。
- `STMS_DATABASE_URL` 是应用和 Alembic 使用的数据库 URL；`STMS_TEST_DATABASE_URL` 只允许 integration 测试使用专用测试库。

没有 `.env` 时，应用也能使用安全的开发默认值启动。

## SQLAlchemy 数据库基础设施

阶段 2 使用 SQLAlchemy 2 同步 API，各组件职责如下：

- `Base`（`app/db/base.py`）只提供共享的 declarative metadata。目前 metadata 为空，不包含用户、认证、项目、任务或其他业务表。
- `Engine`（`app/db/session.py`）管理连接池和数据库方言。它按进程缓存、同步运行，并配置有限的连接超时；创建 Engine 不会立即连接，首次 `connect()` 或 Session 执行 SQL 时才访问 PostgreSQL。
- `sessionmaker` 绑定上述 Engine，用于创建短生命周期的同步 `Session`。`get_session()` 每次 yield 一个 Session，并在 `finally` 中关闭；它不会隐藏 `commit`，未来的写入 use case 必须自行拥有提交/回滚边界。
- readiness 探针使用短生命周期 `Connection` 执行 `SELECT 1`，通过上下文管理器在成功或查询异常时归还连接池。它不创建 Session，也不修改数据。

## PostgreSQL 开发与测试环境

`compose.yaml` 使用固定镜像 `postgres:17.6-bookworm`，并把两个数据库放在同一个明确命名的 `fastapi-stms` Compose 项目中：

| 用途 | Service | 主机端口 | 数据库 | 用户 | 数据策略 |
| --- | --- | --- | --- | --- | --- |
| 开发 | `postgres-dev` | `5432` | `stms` | `stms_dev` | named volume，停止/重建容器后保留 |
| 测试 | `postgres-test` | `5433` | `stms_test` | `stms_test` | `tmpfs`，容器移除后丢弃 |

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

`downgrade` 可能删除或变更 schema，绝不能在开发或生产数据库上把它当作普通检查运行。上面的 fail-fast 命令必须紧邻 downgrade，并严格验证驱动、本地主机、数据库 `stms_test`、用户 `stms_test` 和主机端口 5433。测试 fixture 在每次自动 downgrade 前也执行相同校验。

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

阶段 2 的完整质量门禁是普通 pytest、显式 PostgreSQL integration pytest、Ruff lint、Ruff format、mypy、lock check 和 diff check。SQLite 不作为 PostgreSQL integration 行为的替代品。

## Stage 2 最终验证顺序

下面的流程只操作本项目 Compose 服务。测试库迁移往返前应重建 `postgres-test`，以获得空的 `tmpfs`；不得重建 `postgres-dev` 或删除开发 named volume。

1. 运行 `docker version`、`docker compose version` 和 `docker compose config --quiet`。
2. 运行 `docker compose up -d --wait postgres-dev postgres-test`，确认两个服务均为 `healthy`。
3. 分别通过同步 Psycopg 对开发库和测试库执行 `SELECT 1`，并核对数据库名、当前用户和服务端口。
4. 只重建 `postgres-test`，对空测试库执行 baseline `upgrade head -> downgrade base -> upgrade head`，再运行 `alembic current` 和 `alembic check`。
5. 验证 readiness `200 -> 503 -> 200`，同时确认 liveness 始终为 200。
6. 在不设置数据库环境变量的进程中运行普通 `uv run pytest`，再对专用测试库运行 `uv run pytest -m integration`。
7. 运行全部质量门，审查 Git diff 和迁移漂移。
8. 运行 `docker compose down` 停止并移除本项目容器和网络；不要添加 `--volumes`。随后用 `docker volume inspect fastapi-stms-postgres-dev-data` 确认开发 named volume 仍存在。

## 当前项目结构

```text
FastAPI-STMS/
|-- app/
|   |-- api/
|   |   |-- v1/
|   |   |   |-- endpoints/
|   |   |   |   `-- __init__.py
|   |   |   |-- __init__.py
|   |   |   `-- router.py
|   |   |-- __init__.py
|   |   |-- health.py
|   |   `-- router.py
|   |-- core/
|   |   |-- __init__.py
|   |   `-- config.py
|   |-- db/
|   |   |-- __init__.py
|   |   |-- base.py
|   |   |-- probe.py
|   |   `-- session.py
|   |-- schemas/
|   |   |-- __init__.py
|   |   `-- health.py
|   |-- __init__.py
|   `-- main.py
|-- alembic/
|   |-- versions/
|   |   |-- .gitkeep
|   |   `-- 20260825_0001_stage_2_baseline.py
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
|   |   |-- test_database_connection.py
|   |   |-- test_migrations.py
|   |   `-- test_readiness.py
|   |-- test_alembic_config.py
|   |-- test_config.py
|   |-- test_db_session.py
|   |-- test_health.py
|   |-- test_integration_database_safety.py
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

阶段 1 提供可运行、可测试的应用骨架。阶段 2 已提供同步数据库配置、空 metadata、Session 工厂、Alembic 环境、相互隔离的 PostgreSQL 服务、专用集成测试框架、不创建业务表的 baseline revision，以及数据库感知的 readiness。版本化的 `/api/v1` Router 仍没有产品功能。

下一阶段是 **Stage 3 — Registration**。建议第一项 1–2 小时任务只实现用户 ORM 模型及对应 Alembic migration，并验证邮箱规范化/唯一约束和迁移往返；在项目所有者确认 Stage 2 前不得开始。
