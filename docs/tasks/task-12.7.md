# Task 12.7 — 简历描述、项目讲解与面试问答清单

## 状态

- Planned
- 所属 Stage：Stage 12 — Deployment and job-search presentation
- 前置 Task：Task 12.6（README、架构、演示证据、安全/限制/成本说明均已验收）
- 后续依赖：Stage 12 最终 owner acceptance；MCP、多 Agent和 Version 2 只有在 Stage 12
  验收后才能另行规划。

## 目标

把已经实现并有证据的工程成果转化为诚实、精炼、可追问的求职材料：提供不同长度的
项目介绍、可量化但不夸大的简历 bullet、架构/安全/测试/Agent/RAG 面试问答，以及
“问题 → 代码/测试/文档证据”的学习检查表。完成后，项目 owner 能在简历、README
链接和面试中一致地解释系统，而不是背诵无法证明的宣传语。

本 Task 的主要交付成果是一份求职讲解包，并完成 Stage 12 的文档一致性收尾。

## 当前基线

- 项目是 Python 3.14、FastAPI、Pydantic 2、SQLAlchemy 2 synchronous、PostgreSQL 17、
  Alembic、LangGraph、pgvector、pytest/Ruff/mypy/uv 和 Docker Compose 的模块化单体。
- 已实现注册/login/current user、owner-scoped Project/Task、Agent run/approval/resume/SSE、
  文档上传/索引、lexical+vector RRF retrieval、bounded cited grounding、safe tracing 和
  39-case synthetic offline evaluation。
- Task 12.1 提供 non-root、一键 Compose app；Task 12.2 提供本地 CI workflow。执行时
  必须重新核对是否已有真实远端 CI run，不能沿用计划中的假设。
- Task 12.3～12.6 应提供完整 README、真实架构图、可复现 demo/result 和安全限制说明；
  若任一项未实际验收，本 Task 不得自行补齐其功能。
- 关键可量化证据来自实际测试输出、迁移 head、CI run、39-case baseline 和演示结果。
  数字必须引用具体日期/commit/结果文件；不得把 synthetic latency/token 数据当生产指标。
- 当前明确没有 refresh/logout/password change、事实核验、外部 tracing vendor、公共
  search HTTP、production deployment、MCP、多 Agent 或 Version 2 analytics。
- 仓库仍可能包含多 Task 未提交修改。执行者必须先检查 status/branch/log，不能声称
  某个远端 commit 包含当前工作区。

## 范围

1. 新增求职材料文档，建议为 `docs/job-search.md`（候选路径，先确认无既有同类文件）。
2. 提供三种长度的项目介绍：一句话、30 秒、2 分钟；三者使用相同事实和术语。
3. 提供 3～5 条简历 bullet，每条采用“动作 + 技术决策 + 可验证结果”，并链接到代码、
   测试、迁移、CI 或 eval 证据。
4. 建立面试问答清单，至少覆盖：
   - 为什么用模块化单体与同步 SQLAlchemy；
   - Router/Service/Repository 和事务边界；
   - 用户隔离与数据库复合约束；
   - JWT/secret/日志边界；
   - LangGraph 八节点、审批中断和恢复；
   - Tool identity、approval、idempotency；
   - pgvector、全文检索、RRF 和 filter-before-rank；
   - prompt injection 与 citation fail-closed；
   - checkpoint/audit/SSE/trace 的区别；
   - synthetic evaluation 指标与局限；
   - Docker/CI 和迁移安全；
   - 已知失败模式、成本和下一步改进。
5. 每个回答包含短答、深入追问和真实证据路径；避免粘贴完整代码。
6. 增加 owner 自测清单：能否画调用链、指出 commit/rollback、解释 owner predicate、
   复现 demo、解释一个失败测试、说明未实现内容。
7. 如 README 尚无求职材料入口，最小增加链接；不得重写 Task 12.3 内容。
8. 做 Stage 12 文档一致性审查，核对 roadmap Task 12.1～12.7 的实际状态。只有本 Task
   和所有前置证据都通过时，才能记录 Stage 12 completion；否则保持 planned/partial。

## 非目标

- 不实现任何产品、基础设施、安全改进、MCP、多 Agent 或 Version 2 功能。
- 不修改公共 API、Agent graph、数据库 schema、迁移、Docker 或 CI。
- 不替 owner 投递简历、发布仓库、创建 release、上传视频或联系招聘方。
- 不声称生产部署、真实用户量、SLA、成本节省、模型事实准确率或安全认证。
- 不虚构 GitHub Actions run、测试数量、覆盖率、性能、commit SHA 或外部 Provider 结果。
- 不写通用 FastAPI 面试题大全；所有问题必须与本仓库设计有关。

## 设计与接口约束

- 每条简历量化结果必须来自已保存结果或可复现命令。无法稳定证明的数字改为定性描述。
- 项目调用链按当前实现表述：
  `HTTP → Router → Schema → Service → Repository → SQLAlchemy → PostgreSQL`，以及
  `Agent API → LangGraph node → strict Tool → Domain Service → owner-scoped Repository`。
- 安全回答必须区分 authentication、authorization、approval、idempotency 和 database
  integrity，不能把它们合并成“JWT 保证安全”。
- RAG 回答必须明确 filter-before-rank、fixed RRF 和 citation 非事实保证。
- evaluation 回答必须明确 39 synthetic fake cases、predeclared gate 和未覆盖真实模型。
- 证据路径只引用仓库实际存在文件；候选新增文档在执行时确认后再链接。
- 文档不得包含 secret、Token、个人信息、完整数据库 URL、Prompt/model/document 内容或
  hidden reasoning。
- 不涉及配置、环境变量、数据库或外部服务变化。若希望加入 GitHub badge，必须存在
  与当前 commit 匹配的成功 run；否则不加入。

## 预计修改文件

### 新增文件

- `docs/job-search.md`（候选路径）
- 可选 `tests/test_job_search_documentation.py`（候选；只检查证据链接和禁止夸大模式）

### 修改文件

- `README.md`（仅增加求职材料入口）
- `docs/roadmap.md`（Task 12.7 和 Stage 12 状态必须依据真实验收更新）

### 原则上不应修改的文件

- Task 12.3～12.6 的正文交付物，除非修复一条明确断链且不改变其范围
- `app/**`、`tests/**`（候选文档契约测试除外）、`alembic/**`
- `compose.yaml`、`Dockerfile`、`.github/**`
- `.env.example`、`pyproject.toml`、`uv.lock`

## 实施步骤

1. 读取所有 governing 文档和 Task 12.1～12.6 的最终 acceptance/result，检查
   `git status`、branch、recent commits 和远端 CI 事实。
2. 建立“可用声明清单”：技术决策、实现能力、真实数字、证据路径、限制；删除无法证明
   或仅来自计划的条目。
3. 写一句话/30 秒/2 分钟项目介绍，确保定位一致且不塞入所有技术名词。
4. 写 3～5 条简历 bullet；每条只表达一个主要成果，并附证据注释供 owner 核对。
5. 按架构、安全、Agent/RAG、测试部署四组编写仓库特定 Q&A；每题包含追问与代码路径。
6. 增加 owner 自测清单和 15 分钟演练顺序，连接 Task 12.5 demo 与 Task 12.6 限制。
7. 做交叉链接和夸大声明审查；如前置文档不一致，停止并报告，不在本 Task 重写它们。
8. 运行最小检查和一次最终质量门。根据真实状态更新 roadmap；满足全部 Stage 12 条件后
   才标记完成，随后停止等待 owner acceptance。

## 测试策略

### 1. 最小相关测试

以文档和结构检查为主：

```powershell
rg -n "^#|^##|^###" docs/job-search.md
rg -n "Router|Service|Repository|LangGraph|RRF|citation|approval|idempotency" docs/job-search.md
git diff --check
```

逐个验证证据链接路径存在，并将所有数字与 Task 12.5 results、baseline 或真实 CI run
比对。若新增文档契约测试，仅验证链接/敏感模式/事实锚点，不锁死自然语言。

### 2. 集成测试

不涉及。本 Task 不启动 Docker、Provider 或数据库；引用已验收结果。若 Stage 12 final
状态要求新的运行证据，停止并单独报告缺口，不在求职文档 Task 扩大为功能验收。

### 3. 最终质量门

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
uv lock --check
git diff --check
```

只在最终运行完整门禁一次。成功只记录命令、退出码和摘要；失败保留关键错误和末尾输出。

## 验收标准

- [ ] 一句话、30 秒和 2 分钟介绍对项目定位、实现范围和限制表述一致。
- [ ] 有 3～5 条简历 bullet，每条只含可从仓库或结果文件证明的成果。
- [ ] 面试清单覆盖分层/事务/所有权、Agent/approval、RAG/citation、持久化/可观测性、
      testing/Docker/CI、安全限制和改进成本。
- [ ] 每个技术回答至少链接一个真实代码、测试、迁移或结果证据。
- [ ] 所有数字均与真实结果一致；无虚构 CI、Provider、生产延迟、用户量或覆盖率。
- [ ] 明确列出未实现的 refresh/logout/password change、事实核验、外部 tracing、公共
      search、production cloud、MCP、多 Agent 和 Version 2。
- [ ] README 有且只有简洁入口，不复制整份求职材料。
- [ ] 文档无 secret、Token、个人数据、完整 URL、敏感 payload 或 hidden reasoning。
- [ ] 未修改应用、迁移、Docker、CI、依赖或公共接口。
- [ ] Task 12.1～12.7 状态按真实证据更新，未满足时没有错误标记 Stage 完成。
- [ ] 最小检查和最终质量门通过，Stage 12 停止等待 owner acceptance。

## 停止条件

- 任一前置 Task 未完成或结果不可验证；
- roadmap、README、架构、demo 或安全文档之间存在重大冲突；
- 求职声明需要改变公共接口、功能、安全权限或数据库 schema 才能成立；
- 需要破坏性迁移、新依赖、密钥、Provider/云/GitHub 发布权限；
- owner 必须决定目标岗位、语言、简历篇幅或是否公开具体仓库/CI/视频链接，且选择会显著
  改变交付物；
- 无法证明某个关键数字或成果。

## 执行完成后的报告格式

- 修改文件；
- 求职材料中的调用链/证据流；
- 最小检查与最终质量门；
- 未解决问题；
- 三个学习点；
- 明确 Stage 12 是否具备 owner acceptance 条件，并停止；不得开始 MCP、多 Agent、
  Version 2 或其他后续工作。
