# Task 12.3 — 完整 README、环境配置、API 示例与合成演示资料

## 状态

- Completed（2026-09-07）
- 验收依据：[roadmap Stage 12 的 Task 12.3 验收记录](../roadmap.md#stage-12--deployment-and-job-search-presentation)。最小测试 26 passed，普通测试 884 passed / 67 deselected，质量门通过；以下保留原始任务契约。
- 所属 Stage：Stage 12 — Deployment and job-search presentation
- 前置 Task：Task 12.2（GitHub Actions 本地实现和验证完成）
- 后续依赖：Task 12.4 使用本 Task 确认的产品入口与术语；Task 12.5 复用本 Task
  的启动步骤、API 示例和合成资料。

## 目标

把当前 README 和环境变量说明校准为一份从全新 checkout 可执行的项目入口，并提供
与真实 OpenAPI、权限边界和已实现能力一致的 API 示例及完全合成的演示资料。完成后，
新读者能够安装依赖或使用 Compose 启动服务，理解开发库/测试库隔离，调用主要 HTTP
流程，并明确哪些 Agent、Provider 和认证增强能力尚未实现。

本 Task 的主要交付成果是“可执行的项目使用文档包”，不是新增 API 或演示自动化。

## 当前基线

- 根 `README.md` 已记录 uv、PostgreSQL、注册/登录、当前用户、Project、Task、Agent、
  RAG 和质量命令，但标题仍写成 `Stage 11.1～11.6`，实际已有 Task 11.7/11.8；“当前
  项目结构”也未完整列出 Agent、知识库、评估、Dockerfile 和 CI 文件。
- `.env.example` 已包含应用、数据库、JWT、模型和 embedding 配置的占位值；真实
  `.env` 当前不存在且不得生成或提交。
- `Dockerfile` 与 `compose.yaml` 已支持
  `docker compose up -d --build --wait app`。app 连接内部
  `postgres-dev:5432`，容器启动先运行 `alembic upgrade head`，没有 JWT fallback
  secret；认证使用需要显式注入至少 32 字符的 `STMS_ACCESS_TOKEN_SECRET`。
- `.github/workflows/ci.yml` 已存在未提交的 offline quality 与 PostgreSQL integration
  jobs；当前没有包含该 workflow 的远端 GitHub Actions run，README 不得声称 CI 已
  在 GitHub 通过。
- 当前公开入口由 `app/api/router.py` 和 `app/api/v1/router.py` 组合：health、auth、
  users、projects、tasks、agent runs 和 knowledge documents。不存在 refresh/logout、
  公共 knowledge search、MCP 或多 Agent HTTP 接口。
- `app/main.py::create_app` 根据 `STMS_API_DOCS_ENABLED` 控制 `/docs`、`/redoc` 和
  `/openapi.json`。
- Stage 11 已有 `evals/stage11/dataset.v1.jsonl`（39 个合成案例）、
  `baseline.v1.json` 和说明；普通测试不访问外部 Provider。
- Git 工作区包含 Stage 11、Task 12.1 和 Task 12.2 的未提交修改。执行者必须先查看
  `git status`，不能覆盖、回退或提交这些修改，除非 owner 在新指令中明确授权。

## 范围

1. 按当前代码修订 README 的项目定位、已实现能力、限制、目录结构和 Stage 状态。
2. 给出两条互不混淆的启动路径：本机 uv 开发运行，以及 Task 12.1 的一键 Compose
   app 启动；明确停止命令保留开发 volume。
3. 完整解释 `.env.example` 中现有变量的用途、是否必需、敏感性和适用路径。不得加入
   真实值；明确未配置 JWT secret 或 Provider key 时哪些路径仍可用。
4. 提供可复制的 PowerShell/curl API 示例，覆盖 health、注册、登录、当前用户、
   Project、Task、知识文档上传/索引和 Agent run/审批/SSE 的代表流程。
5. 每个示例必须与实际请求 Schema、HTTP 方法、状态码和路由一致；认证示例只使用
   明显的占位 Token，不能把完整 Token 写入文档或命令历史建议。
6. 新增一份无版权、无个人信息、无外部指令依赖的合成学习资料，供 Task 12.5 上传
   演示。建议路径为 `docs/demo/sample-syllabus.md`（候选路径，执行时先确认仓库没有
   既有 demo 目录或命名规范）。内容必须明确标记 synthetic。
7. 说明知识索引和 Agent 运行何时需要模型/embedding 配置，基础 API、health 和离线
   评估何时不需要；不得引导普通测试访问真实 Provider。
8. 记录 Task 12.2 的真实状态：本地 workflow 已验证但尚无远端 run；只有实际 push
   并观察成功 run 后才能加入 CI badge 或“CI passed”表述。

## 非目标

- 不新增或修改 Router、Schema、Service、Repository、ORM、迁移或业务测试。
- 不编写自动 seed、数据库直写或自动调用真实 Provider 的脚本；可执行演示编排属于
  Task 12.5。
- 不生成架构图或 LangGraph 状态图；属于 Task 12.4。
- 不编写录制脚本、视频或结果报告；属于 Task 12.5。
- 不集中撰写失败模式、安全声明、价格或改进路线；属于 Task 12.6。
- 不撰写简历和面试材料；属于 Task 12.7。
- 不实现 refresh token、logout、密码修改、grounded claim 事实核验、外部 tracing、
  公共 search HTTP、MCP、多 Agent、云部署或 Version 2。

## 设计与接口约束

- 预期调用链必须按实际入口表述：
  `HTTP → Router/dependency → Schema → Service → Repository → synchronous SQLAlchemy → PostgreSQL`；
  Agent 写链路补充 `HTTP Agent API → LangGraph node → Agent Tool → Domain Service`。
- README 只能描述代码中已经存在的接口。执行时以 FastAPI OpenAPI 和端点装饰器为
  最终依据，不能从旧 README 反推代码。
- 示例输入必须满足真实长度、枚举、分页、日期时区和所有权约束；响应示例只能包含
 公开 Schema 字段。
- 错误示例必须使用现有公开格式和安全消息，不展示 SQL、数据库 URL、密码、JWT、
  API key、完整 Prompt、文档原文、embedding 或隐藏推理。
- 合成演示资料始终是不可信数据示例，不得包含会被误认为系统策略、凭据或真实个人
  信息的内容。
- 配置边界：文档可以列出现有变量，但不得新增环境变量。若文档需求看似需要新变量，
  停止并报告。
- 数据库边界：不执行迁移往返、清库或 seed；不涉及数据库 schema 变化。
- 外部服务边界：不调用真实模型、embedding API、GitHub API 或其他网络服务。
- 保持 Windows PowerShell 示例与项目现有命令一致；如同时提供 POSIX 示例，不能替代
  已验收的 PowerShell 主路径。

## 预计修改文件

### 新增文件

- `docs/demo/sample-syllabus.md`（候选路径；执行时先确认命名和目录）。
- 可选 `tests/test_documentation_contracts.py`（候选路径；仅当需要自动验证 README
  路由/秘密占位符且现有测试无法覆盖时；不得测试文案措辞本身）。

### 修改文件

- `README.md`
- `.env.example`（仅修正注释、分组或缺失的现有变量说明，不新增功能配置）
- `docs/roadmap.md`（仅在所有验收完成后追加 Task 12.3 的真实 acceptance status）

### 原则上不应修改的文件

- `app/**`
- `alembic/**`、`alembic.ini`
- `compose.yaml`、`Dockerfile`、`.dockerignore`
- `.github/workflows/ci.yml`
- `pyproject.toml`、`uv.lock`
- 现有业务、integration 和 Agent 测试

## 实施步骤

1. 读取 governing 文档，记录 `git status`、分支和最近提交；确认 Task 12.2 文件存在，
   但不要把本地实现写成远端通过。
2. 从路由装饰器、请求/响应 Schema、配置类、Compose 和 pytest marker 建立当前能力
   清单，并逐段标记 README 的过期或重复内容。
3. 设计 README 信息顺序：项目价值 → 快速启动 → 配置 → API 示例 → Agent/RAG →
   测试/CI → 安全限制 → 项目结构。合并重复命令，保留危险数据库操作警告。
4. 用实际 Schema 校验每个请求/响应示例，确保路径、方法、状态码和字段一致。
5. 新增最小 synthetic sample syllabus；限制长度，不加入版权文本、个人数据、秘密或
   prompt-injection 指令。
6. 更新 `.env.example` 注释，使必需/可选和本机/Compose/test/Provider 边界明确。
7. 如确有价值，增加最小静态契约测试；否则使用结构检查、OpenAPI 现有测试和秘密扫描。
8. 运行文档相关检查和一次最终质量门，审查 diff 只包含本 Task 文档范围。
9. 验收通过后更新 roadmap 状态并停止。

## 测试策略

### 1. 最小相关测试

```powershell
uv run pytest -q tests/test_main.py tests/test_config.py tests/test_docker_assets.py tests/test_ci_workflow.py
```

若新增文档契约测试，将其加入该命令。使用 `rg` 检查 README 中列出的路由和文件路径；
检查示例不包含真实 Token、密码、API key 或完整数据库 URL。

### 2. 集成测试

本 Task 不改变运行代码或数据库，原则上不需要启动 Docker 或重跑完整 integration。
若现有 OpenAPI/路由证据无法验证示例，可使用 FastAPI TestClient 的普通测试；不得仅为
文档启动 `postgres-test`。发现接口与文档要求冲突时停止，不要在本 Task 修代码。

### 3. 最终质量门

遵循“修改 → 最小测试 → 修复 → 相关测试 → 最终完整质量门一次”：

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

成功时记录命令、退出码和摘要；失败时保留关键错误和末尾输出，不得隐藏失败。

## 验收标准

- [ ] README 的 Stage、能力、限制和目录结构与当前仓库一致，且不再将已实现 Agent/RAG
      描述为 future。
- [ ] 全新读者可从 README 找到本机 uv 与一键 Compose 两条启动路径，以及保留 volume
      的停止命令。
- [ ] `.env.example` 的现有变量均有清晰边界，文件和 README 不含真实 secret。
- [ ] 至少一个完整认证/Project/Task 示例和一个知识/Agent 示例与实际 OpenAPI 匹配。
- [ ] 示例响应不含 `user_id`、`password_hash`、完整 Token、私有文档、embedding、SQL
      或隐藏推理等非公开字段。
- [ ] synthetic sample 文件存在、可作为允许的知识文档输入、无个人/版权/秘密内容。
- [ ] README 明确区分“Task 12.2 本地 workflow 已验证”与“远端 GitHub Actions run”。
- [ ] 所有引用的现有文件路径通过结构检查；候选新增路径在执行时已确认。
- [ ] 未修改应用、迁移、Docker、CI、依赖或业务测试。
- [ ] 最小测试和最终质量门通过，`git diff --check` 为 0。

## 停止条件

出现以下任一情况，停止并报告，不得扩大范围：

- roadmap 与实际接口存在会改变公开契约的重大冲突；
- 示例正确性要求修改已确认的公共 API；
- 需要数据库 schema 变化、破坏性迁移或清理开发数据；
- 需要新的密钥、账户、GitHub push 或外部权限；
- Task 12.2 workflow、安全守卫或本地验收实际不完整；
- demo data 的形式需要 owner 在“静态合成资料”和“可写 seed 脚本”之间做产品选择。

## 执行完成后的报告格式

未来执行者必须报告：

- 修改文件；
- 文档中的 HTTP/Agent 调用链；
- 最小测试与最终质量门的命令、退出码和摘要；
- 未解决问题；
- 三个学习点；
- 明确已停止，未进入 Task 12.4。
