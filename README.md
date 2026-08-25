# FastAPI STMS

FastAPI STMS 是一个分阶段构建的学习项目。当前完成了阶段 1 应用骨架，以及阶段 2 的同步 SQLAlchemy 基础设施、Alembic 环境、PostgreSQL 开发/测试服务和专用集成测试框架。

目前尚未实现业务数据库表、migration revision、认证、用户、项目、任务、readiness 或 CI。不要把当前仓库当作完整的任务管理系统。

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

没有 `.env` 时，应用也能使用安全的开发默认值启动。

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

停止并移除本项目容器和网络，同时保留开发数据 volume：

```powershell
docker compose down
```

不要为普通停止添加 `--volumes`，否则会删除开发数据库的 named volume。测试数据库使用独立的 `tmpfs`，不会复用或清空开发数据。

### 单独运行 PostgreSQL 集成测试

只启动专用测试数据库，不启动或修改开发数据库：

```powershell
docker compose up -d --wait postgres-test
$env:STMS_TEST_DATABASE_URL = "postgresql+psycopg://stms_test:stms_test_local@127.0.0.1:5433/stms_test"
uv run pytest -m integration tests/integration/test_database_connection.py
```

集成测试会在建立 Engine 前拒绝缺失、非 `postgresql+psycopg`、非 `*_test`、与 `STMS_DATABASE_URL` 相同、指向 `stms` 或与开发库复用主机端口的 URL。错误信息不会输出完整 URL 或密码。

验证后只停止测试服务；此命令不会停止 `postgres-dev`，也不会删除或修改开发 named volume：

```powershell
docker compose stop postgres-test
Remove-Item Env:STMS_TEST_DATABASE_URL
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

也可以依次运行以上四条命令，作为当前项目的完整质量门禁。

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
|   |   `-- session.py
|   |-- schemas/
|   |   |-- __init__.py
|   |   `-- health.py
|   |-- __init__.py
|   `-- main.py
|-- alembic/
|   |-- versions/
|   |   `-- .gitkeep
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
|   |   `-- test_database_connection.py
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

阶段 1 提供可运行、可测试的应用骨架。阶段 2 当前已提供同步数据库配置、空 metadata、Session 工厂、Alembic 环境、相互隔离的 PostgreSQL 服务，以及只连接专用测试库的集成测试框架。版本化的 `/api/v1` Router 仍没有产品功能。

roadmap 的下一项是 **Task 2.6 — Initial baseline migration and round-trip verification**。在项目所有者确认前，不应开始该任务。
