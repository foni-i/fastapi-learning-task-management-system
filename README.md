# FastAPI STMS

FastAPI STMS 是一个分阶段构建的学习项目。当前只完成了阶段 1：FastAPI 应用骨架、类型化配置、版本化 API Router 骨架、存活检查，以及 pytest、Ruff 和 mypy 质量检查。

目前尚未实现数据库、迁移、认证、用户、项目、任务、Docker 或 CI。不要把当前仓库当作完整的任务管理系统。

## 前置条件

- Git
- Windows PowerShell 5.1 或 PowerShell 7+
- 首次安装工具和依赖时可访问互联网
- `uv`；项目所需的 Python 3.14 由 `uv` 管理

阶段 1 不需要 PostgreSQL 或 Docker。

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
- 当前示例支持运行环境、调试模式和 API 文档开关。

没有 `.env` 时，应用也能使用安全的开发默认值启动。

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

运行测试：

```powershell
uv run pytest
```

运行 Ruff 静态检查和格式检查：

```powershell
uv run ruff check .
uv run ruff format --check .
```

运行 mypy 严格类型检查：

```powershell
uv run mypy app tests
```

也可以依次运行以上四条命令，作为阶段 1 的完整质量门禁。

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
|   |-- schemas/
|   |   |-- __init__.py
|   |   `-- health.py
|   |-- __init__.py
|   `-- main.py
|-- docs/
|   |-- architecture.md
|   |-- requirements.md
|   `-- roadmap.md
|-- tests/
|   |-- __init__.py
|   |-- conftest.py
|   |-- test_config.py
|   |-- test_health.py
|   `-- test_main.py
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

阶段 1 仅提供可运行、可测试、经过静态检查的应用骨架。版本化的 `/api/v1` Router 已挂载，但没有产品功能。

roadmap 的下一阶段是 **Stage 2 — PostgreSQL and migrations**，将从建立 SQLAlchemy 2 同步数据库基础设施开始。在项目所有者确认前，不应开始阶段 2。
