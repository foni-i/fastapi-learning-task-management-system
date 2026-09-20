# 工程任务索引：Stage 12、checkpoint remediation 与认证加固

## 已完成的 Stage 12 目标

Stage 12 将已经通过功能、安全、迁移和离线评估验收的 StudyFlow Agent
整理成可复现启动、可持续验证、可演示的工程成果。它不增加新的
业务能力，也不开始 MCP、多 Agent、Version 2 或云部署。

## 已完成 Task

- Task 12.1：已实现固定 Python 3.14/uv 镜像、非 root app service、
  `postgres-dev` 健康依赖、Alembic 先行启动和一键 Compose 启动。
- Task 12.2：两 job GitHub Actions workflow 覆盖离线质量门和受保护的 PostgreSQL 17 +
  pgvector integration；checkpoint commit `b05adae` 的
  [远端 run 35097448961](https://github.com/foni-i/fastapi-learning-task-management-system/actions/runs/35097448961)
  已实际运行且两个 job 均成功。
- Task 12.3～12.6：Completed（2026-09-07），已完成本地验收；详见
  [roadmap Stage 12 验收记录](../roadmap.md#stage-12--deployment-and-engineering-presentation)。

## 本次生成的 Task

| Task | 状态 | 前置依赖 | 一句话可见成果 |
| --- | --- | --- | --- |
| [Task 12.3](task-12.3.md) | Completed | Task 12.2 | 新用户可按完整 README、环境变量说明、API 示例和合成示例资料复现项目。 |
| [Task 12.4](task-12.4.md) | Completed | Task 12.3 | 架构文档用与代码一致的系统、数据流和八节点 LangGraph 图解释实现。 |
| [Task 12.5](task-12.5.md) | Completed | Task 12.4 | 一份可重复执行的演示/录制手册展示启动、核心流程、测试和离线评估证据。 |
| [Task 12.6](task-12.6.md) | Completed | Task 12.5 | 一份诚实的失败模式、安全边界、改进路线与成本说明可供评审。 |

## Checkpoint remediation

Stage 11～Task 12.6 的统一候选 checkpoint 曾在独立提交前审查中被阻塞；R1～R6 已按序
完成、复审、提交并推送，匹配的远端 CI 已成功。以下整改项是该 checkpoint 的历史门禁，
不属于 Stage 12 新功能，也不重新编号既有 Task。

| Remediation | 状态 | 前置依赖 | 对应 Finding | 一句话结果 |
| --- | --- | --- | --- | --- |
| [R1 — CI runtime parity](checkpoint-remediation-r1-ci-runtime.md) | Completed | 当前候选 checkpoint | P1：integration job 缺少测试 JWT secret | CI integration 使用 job-scoped 合成测试 secret，并由静态契约和真实 66-test 命令共同证明。 |
| [R2 — Compose network boundary](checkpoint-remediation-r2-compose-network.md) | Completed | R1 | P1：默认发布到所有宿主接口 | app 和两个 PostgreSQL 服务默认只绑定 127.0.0.1，端口 override 能力保留。 |
| [R3 — Ingestion boundaries](checkpoint-remediation-r3-ingestion-boundaries.md) | Completed | R2 | P1：multipart 前无总 body 限制；P2：U+0000 写库失败 | 纯 ASGI 流式入口限制与持久化文本校验分别稳定返回 413/422。 |
| [R4 — Deterministic prompt budget](checkpoint-remediation-r4-prompt-budget.md) | Completed | R3 | P2：最终 prompt 可超过 20,000 字符 | 最终序列化输入采用分区预算、Unicode 安全确定性截断和显式 truncation manifest。 |
| [R5 — Durable approval recovery](checkpoint-remediation-r5-approval-recovery.md) | Completed | R4 | P1：审批 commit 与 resume 间崩溃窗口 | 以 run-scoped 锁、checkpoint inspection 和工具幂等实现可竞争、可重试恢复。 |
| [R6 — Evaluation integrity](checkpoint-remediation-r6-evaluation-integrity.md) | Completed | R5 | P2：evaluation fixture 自证 | 输入进入真实 graph/validation/recovery 路径，observations 与 expectations 严格分离。 |

R5 已比较 durable transition/outbox 与基于现有审批记录/checkpoint 的幂等恢复协议，
并实现后者；锁定 LangGraph 版本的公开 continuation 语义及竞争/故障恢复已经测试验证。

## 推荐执行顺序

```text
Task 12.1（已完成）
→ Task 12.2（已完成，远程 CI 已验证）
→ Task 12.3～12.6（Completed）
→ Stage 11～Task 12.6 独立提交前审查（曾 BLOCK）
→ R1 → R2 → R3 → R4 → R5 → R6（Completed）
→ 独立复审 → checkpoint commit b05adae → push
→ GitHub Actions run 35097448961（success）
→ Stage 12 工程交付完成
```

每个 Task 应在独立 Codex 任务窗口执行并单独验收。若执行者要引用 GitHub Actions
成功状态，必须先确认 Task 12.2 已经 commit/push 且远端确有对应 run；不得把本地
workflow 测试当成远端 CI 通过。

当前保留全部前置实现和工程规划文档。
原任务文档保留执行范围与验收要求；Completed 的完成证据以 roadmap 为准。
审批 proposal 预览 P0 已由
[Security P0.1](security-p0-approval-preview.md) 完成；它保持独立 endpoint、现有审批
POST 与数据库 schema 不变。

不得自行 commit、push，或开始 MCP、多 Agent、Version 2 与其他后续实现。

## 独立安全加固

| Task | 状态 | 前置依赖 | 一句话可见成果 |
| --- | --- | --- | --- |
| [Security P0.1 — Approval preview](security-p0-approval-preview.md) | Completed | Stage 12、R5、R6 | Owner 在审批前读取与 revision/fingerprint/实际执行一致的完整、有界、类型化 proposal preview。 |

该 Task 已按 owner 确认的 endpoint、公开字段和 65,536-byte fail-closed 边界完成并通过
真实 PostgreSQL integration；完成不授权开始 MCP、多 Agent、Version 2 或生产部署。

## 当前工程任务

恢复 [roadmap Stage 5](../roadmap.md#stage-5--deferred-authentication-hardening)
延期保留的认证加固路线，逐项实施和验收。

| Task | 状态 | 前置依赖 | 一句话可见成果 |
| --- | --- | --- | --- |
| [Task 5.1 — Refresh Token 模型与迁移](stage-5-1-refresh-token-model.md) | Completed / owner 已验收 | Owner 已确认契约；现有认证基线及完整迁移链 | 6 字段私有表、具名约束及用户索引已实现；真实 PostgreSQL 迁移往返和全套 integration 通过。 |
| [Task 5.2 — Refresh Token 内部签发](stage-5-2-refresh-token-issuance.md) | Completed / 验收后 owner 授权继续 | Task 5.1 已验收，owner 授权继续 | 安全随机令牌、摘要入库、提交后交付与事务失败回滚；完整 integration 97 passed。 |
| [Task 5.3 — 原子轮换与重用拒绝](stage-5-3-refresh-token-rotation.md) | Completed / owner 验收后继续 | Task 5.2 验收后继续 | 原子轮换与重放拒绝；新增 PostgreSQL 11 passed、完整 integration 108 passed。 |
| [Task 5.4 — Refresh HTTP 与双令牌交付](stage-5-4-refresh-http.md) | Completed / owner 验收后继续 | Task 5.3 已验收，owner 已确认 JSON 契约 | 定向离线 97、完整离线 1017、新增 PostgreSQL HTTP 11、完整 integration 119 项均通过。 |
| [Task 5.5 — Logout 单凭据吊销](stage-5-5-logout.md) | Completed / owner 验收后继续 | Task 5.4 验收后 owner 授权继续 | 幂等退出只吊销所提交的刷新凭据；新增离线 28、真实退出 HTTP 10、完整 integration 129 项通过。 |
| [Task 5.6 — 密码修改与全部刷新凭据吊销](stage-5-6-password-change.md) | Completed / owner 验收后继续 | Task 5.5 验收后 owner 授权继续 | 单事务改密及全部刷新吊销；定向 PostgreSQL 56、完整 integration 149、离线 1078 与全部质量门通过。 |
| [Task 5.7 — 认证安全集成与文档收尾](stage-5-7-auth-security-acceptance.md) | Completed / owner 已验收 | Task 5.6 已验收 | 新增安全验收 13、完整 integration 162、离线 1078 项通过，质量门及迁移复查通过。 |
| [Stage 5 checkpoint 提交准备](stage-5-checkpoint-preparation.md) | Prepared / 等待 owner 确认 | Task 5.1～5.7 已验收 | 52 项候选清单，integration 162、离线 1078 与质量门通过；暂存区为空，未 commit/push。 |

2026-09-17 Task 5.1 已按确认契约实现，新 head 为 `86cd95365562`；定向离线 41 passed、
新增 PostgreSQL 21 passed、全套 integration 93 passed、完整离线 944 passed；最终质量门
通过。详细结果见任务文档与 roadmap。Owner 已验收 Task 5.1；Task 5.2 已实现并通过
19 项定向离线测试、4 项新增真实事务测试、97 项全套 integration、963 项完整离线测试
和全部质量门，验收后 owner 授权继续，未 commit/push。
Task 5.3 已补齐真实并发/回滚验证：定向离线 38 passed、完整离线 982 passed，质量门通过。
Docker 恢复可用后，以进程级测试端口 55433 避开 Windows 保留端口；测试容器已恢复停止。
Owner 在 Task 5.3 验收报告后授权继续，未 commit/push。
Task 5.4 的 JSON body 契约已实现；用户重启 Docker 后真实数据库验证与全部质量门通过。
仅启动测试库并恢复停止，唯一 head `86cd95365562`、无漂移；owner 在报告后授权继续。
Task 5.5 已实现并通过验证，完整离线 1045 passed、质量门通过，owner 在报告后授权继续；
Task 5.6 已实现；2026-09-19 owner 重启 Docker 后补齐真实验证，完整 integration 149
passed、迁移无漂移、离线质量门通过。仅操作 postgres-test 并恢复停止，owner 已确认继续。
Task 5.7 已完成本地安全集成与证据整理，未修改应用代码；仅操作 postgres-test 并恢复
停止。Owner 随后验收并明确授权 Stage 5 checkpoint 提交准备；该授权不包含实际提交、
推送或任何新功能。历史任务结果保留，当前提交准备证据由独立记录承载。
当前登录交付双令牌，refresh 仅接受 JSON body。未 commit/push 或运行本次改动的远程 CI。
