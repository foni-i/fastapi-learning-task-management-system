# Stage 12 task plan and checkpoint remediation

## 当前 Stage 目标

Stage 12 将已经通过功能、安全、迁移和离线评估验收的 StudyFlow Agent
整理成可复现启动、可持续验证、可演示且适合求职说明的项目成果。它不增加新的
业务能力，也不开始 MCP、多 Agent、Version 2 或云部署。

## 已完成 Task

- Task 12.1：已实现固定 Python 3.14/uv 镜像、非 root app service、
  `postgres-dev` 健康依赖、Alembic 先行启动和一键 Compose 启动；修改尚未提交。
- Task 12.2：已在本地实现两 job GitHub Actions workflow，覆盖离线质量门和
  受保护的 PostgreSQL 17 + pgvector integration；修改尚未提交或 push，因而尚无
  包含本次 workflow 的 GitHub Actions run。
- Task 12.3～12.6：Completed（2026-09-07），已完成本地验收；详见
  [roadmap Stage 12 验收记录](../roadmap.md#stage-12--deployment-and-job-search-presentation)。

## 本次生成的 Task

| Task | 状态 | 前置依赖 | 一句话可见成果 |
| --- | --- | --- | --- |
| [Task 12.3](task-12.3.md) | Completed | Task 12.2 | 新用户可按完整 README、环境变量说明、API 示例和合成示例资料复现项目。 |
| [Task 12.4](task-12.4.md) | Completed | Task 12.3 | 架构文档用与代码一致的系统、数据流和八节点 LangGraph 图解释实现。 |
| [Task 12.5](task-12.5.md) | Completed | Task 12.4 | 一份可重复执行的演示/录制手册展示启动、核心流程、测试和离线评估证据。 |
| [Task 12.6](task-12.6.md) | Completed | Task 12.5 | 一份诚实的失败模式、安全边界、改进路线与成本说明可供评审。 |
| [Task 12.7](task-12.7.md) | Planned | Task 12.6 | 简历项目描述、项目介绍和证据可追溯的面试问答清单完成 Stage 12。 |

## Checkpoint remediation

Stage 11～Task 12.6 的统一候选 checkpoint 经独立提交前审查后处于 blocked 状态。以下
整改项是提交该 checkpoint 前的独立任务，不属于 Stage 12 新功能，不重新编号既有 Task，
也不改变 Task 12.7 的求职材料范围。每项仍按 1～2 小时、单独实施、单独验收和停止的
工作协议执行。

| Remediation | 状态 | 前置依赖 | 对应 Finding | 一句话结果 |
| --- | --- | --- | --- | --- |
| [R1 — CI runtime parity](checkpoint-remediation-r1-ci-runtime.md) | Planned | 当前候选 checkpoint | P1：integration job 缺少测试 JWT secret | CI integration 使用 job-scoped 合成测试 secret，并由静态契约和真实 66-test 命令共同证明。 |
| [R2 — Compose network boundary](checkpoint-remediation-r2-compose-network.md) | Planned | R1 | P1：默认发布到所有宿主接口 | app 和两个 PostgreSQL 服务默认只绑定 127.0.0.1，端口 override 能力保留。 |
| [R3 — Ingestion boundaries](checkpoint-remediation-r3-ingestion-boundaries.md) | Planned | R2 | P1：multipart 前无总 body 限制；P2：U+0000 写库失败 | 纯 ASGI 流式入口限制与持久化文本校验分别稳定返回 413/422。 |
| [R4 — Deterministic prompt budget](checkpoint-remediation-r4-prompt-budget.md) | Planned | R3 | P2：最终 prompt 可超过 20,000 字符 | 最终序列化输入采用分区预算、Unicode 安全确定性截断和显式 truncation manifest。 |
| [R5 — Durable approval recovery](checkpoint-remediation-r5-approval-recovery.md) | Planned | R4 | P1：审批 commit 与 resume 间崩溃窗口 | 以 run-scoped 锁、checkpoint inspection 和工具幂等实现可竞争、可重试恢复。 |
| [R6 — Evaluation integrity](checkpoint-remediation-r6-evaluation-integrity.md) | Planned | R5 | P2：evaluation fixture 自证 | 输入进入真实 graph/validation/recovery 路径，observations 与 expectations 严格分离。 |

R5 已在规划中比较 durable transition/outbox 与基于现有审批记录/checkpoint 的幂等恢复
协议，并选择后者作为当前最小可靠方案。实现前仍必须以锁定 LangGraph 版本的公共 API
验证 continuation 语义；若该验证失败，按 R5 停止条件返回 owner 重新选择架构。

## 推荐执行顺序

```text
Task 12.1（已完成）
→ Task 12.2（本地完成，远程 CI 尚未验证）
→ Task 12.3～12.6（Completed）
→ Stage 11～Task 12.6 独立提交前审查（BLOCK）
→ R1 → R2 → R3 → R4 → R5 → R6
→ 独立复审
→ checkpoint commit
→ push
→ 远程 GitHub Actions
→ Task 12.7（Planned，等待 owner 单独授权执行）
→ Stage 12 owner acceptance
```

每个 Task 应在独立 Codex 任务窗口执行并单独验收。若执行者要引用 GitHub Actions
成功状态，必须先确认 Task 12.2 已经 commit/push 且远端确有对应 run；不得把本地
workflow 测试当成远端 CI 通过。

当前保留全部前置实现和规划文档，统一基线包含 Task 12.7 的计划，不包含其功能实现。
原任务文档保留执行范围与验收要求；Completed 的完成证据以 roadmap 为准。
审批 proposal 预览 P0 已登记为 [待排期安全 backlog](../roadmap.md#unscheduled-security-backlog)，
尚未实现或编号，不改变 Task 12.7 范围。

整改规划文档必须在候选 checkpoint 暂存区之外保持可区分，直到 owner 明确授权各项实现
和最终提交。规划阶段不取消既有暂存、不 commit、不 push，也不开始 Task 12.7。
