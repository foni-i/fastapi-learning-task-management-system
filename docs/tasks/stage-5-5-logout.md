# Task 5.5 — Logout：吊销所提交的 Refresh Token

## 状态与范围

Completed / 本地验证通过，owner 在验收报告后要求继续 Task 5.6。Owner 在 Task 5.4 验证报告后要求继续下一个任务；本次仅实现 roadmap
Task 5.5。不实现 Task 5.6 密码修改、全设备退出、token family、Access denylist，
不 commit/push。Task 5.4 视为验收后继续，历史验证记录保留。

## HTTP 与安全契约

- `POST /api/v1/auth/logout` 仅接受 JSON `refresh_token`，复用严格 43 字符
  URL-safe SecretStr schema；不接受额外 user_id，不从 Cookie/query/Bearer 取凭据。
- 不要求有效 Access Token。身份由所持刷新凭据的摘要查找确定；更新仍同时约束
  数据库记录 ID 和其真实 owner，不使用调用方身份参数。
- 存在且未撤销的记录标记撤销；已过期记录也可撤销。不存在或已撤销均空 `204`，
  不透露凭据状态；重复请求不改变原撤销时间，不产生新令牌或删除记录。
- 非法请求为有界脱敏 `422`；数据库/依赖/提交故障统一安全 `503`，不假报退出成功。
  成功和已处理的失败均 `Cache-Control: no-store`、`Pragma: no-cache`，不设置 Cookie。
- 已签发 Access Token 保持原 TTL。其他用户、同用户其他登录和已轮换的后继均不受影响。
  客户端应串行处理刷新/退出并提交最新刷新凭据；本任务不是“撤销整条会话链”。

## 事务、时间与并发

Router → `logout_refresh_token` → SHA-256 → `get_by_hash_for_update` →
owner-scoped `revoke_if_unrevoked` → SQLAlchemy/PostgreSQL → 单次 commit → 空 204。
Service 持有独立用例事务，异常 rollback（回滚诊断也不外泄）；Repository 不提交。
沿用 `FOR UPDATE`、`populate_existing` 和 5 秒锁等待上限，与 refresh 锁定同一行。
锁后采样 UTC；系统时钟倒退时取 `max(now, created_at)`，满足数据库约束且立即让
`revoked_at IS NOT NULL`，不因时钟异常返回虚假成功或重新激活凭据。

| 首先获得行锁 | 后续请求 | 结果 |
| --- | --- | --- |
| logout | refresh | 204 后 401，没有后继 |
| refresh | logout 旧凭据 | 200 后 204，后继仍有效 |
| logout | logout | 两次 204，仅一次状态改变 |

事务提交确认或 HTTP 响应丢失可重试相同退出请求；不能据此声称所有网络故障均可判定，
也不能用旧凭据退出保证并发刷新已产生的后继被撤销。没有新增迁移、配置或依赖。

## 验收与执行记录

最小离线测试 → 仅启动 postgres-test（进程端口 55433）→ target guard →
Alembic upgrade/current/heads/check → 定向及完整非 Provider integration → 迁移复查 →
完整离线 pytest、lock、Ruff、mypy、Git diff/敏感信息检查 → 恢复测试服务停止。
不操作 app/postgres-dev 或开发卷；保留既有未暂存修改，暂存区原为空。

测试覆盖幂等、未知/过期/已撤销/时钟倒退、owner/同 owner 会话隔离、旧 Access 有效、
错误输入/来源/脱敏/debug/缓存头/OpenAPI、提交前失败回滚及重试；真实 PostgreSQL
通过 `pg_blocking_pids` 证明三个并发顺序发生实际行锁竞争。

### 实际验证（2026-09-18）

本轮修改应用四个文件：auth endpoint、main 校验边界、refresh service/repository；
新增 `tests/test_logout.py`，扩展既有 `tests/integration/test_refresh_http.py`，同步
test_main 与 test_refresh_token_model 的路由契约。同步 README、architecture、
security-and-limitations、roadmap、tasks README、Task 5.4 状态及本任务文档。
没有修改模型、schema、迁移、依赖、Compose、真实环境文件或 CI。

| 命令/检查 | 退出码 | 实际结果 |
| --- | --- | --- |
| `uv run pytest -q tests/test_logout.py tests/test_refresh_api.py tests/test_refresh_token_rotation.py tests/test_refresh_token_model.py tests/test_main.py --tb=line` | 0 | 88 passed；新增退出离线用例 28 项 |
| `docker compose ps -a`（验证前） | 0 | app、postgres-dev、postgres-test 原均 exited |
| `docker compose up -d --wait postgres-test` | 0 | 使用进程级 55433，healthy |
| migration target guard（套件前） | 0 | 专用回环地址/测试端口/stms_test，通过 |
| `uv run alembic upgrade head` | 0 | 从空测试 tmpfs 升至 head |
| `uv run alembic current` / `uv run alembic heads` | 各 0 | 唯一 head `86cd95365562` |
| `uv run alembic check` | 0 | No new upgrade operations detected |
| `uv run pytest -m "integration and not external_provider" tests/integration/test_refresh_http.py --tb=line` | 0 | 21 passed：11 项既有刷新 + 10 项新增退出 |
| `uv run pytest -m "integration and not external_provider" tests/integration --tb=line` | 0 | 129 passed，无真实 Provider |
| `uv lock --check` | 0 | 98 packages |
| `uv run pytest -q --tb=line` | 0 | 1045 passed，130 deselected |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 268 files already formatted |
| `uv run mypy app tests alembic` | 0 | 239 source files |
| `git diff --check` / `git diff --cached --check` | 各 0 | 空暂存区保留 |
| 已修改新增行/暂存新增行/未跟踪文件敏感模式扫描 | 0 | 完整 JWT、带凭据数据库 URL、私钥及常见 Provider/GitHub key 模式均无命中 |

首次 Ruff lint 退出 1（新增导入顺序），首次 mypy 退出 1（测试 Mock 返回值与
append 表达式类型）；已作最小修正并完整重跑通过。最小测试和定向 integration 首次即通过。
上述扫描是有界启发式检查，不等同于完整生产秘密检测体系；人工审查本任务差异没有
发现无关改动、原始凭据持久化、Repository 提交或扩大退出范围。原有未暂存修改保留，
没有 stage/commit/push 或远程 GitHub Actions 运行。

套件后 target guard、`alembic upgrade head`、`current`、`heads`、`check` 均退出 0；
仍为唯一 head `86cd95365562`，没有 schema 漂移。`docker compose stop postgres-test`
退出 0，随后 `docker compose ps -a` 退出 0，测试服务恢复 exited；app 和 postgres-dev
始终保持原来的 exited，未操作开发卷。最小离线 88 项在类型修正后再次退出 0。

保留风险：Access JWT 不立即失效、并发刷新先完成时后继不被旧凭据退出撤销；尚无
密码修改/全会话吊销/限流。Windows 测试端口仍使用进程级 55433，未改变系统保留端口，
本轮 Docker 可用不代表历史 socket 故障永久修复。Task 5.6 未开始，等待 owner 验收。

## 学习点

1. 退出的范围是提交的刷新凭据，不能等同于立即使所有 JWT 或所有设备失效。
2. 幂等响应兼顾安全重试与隐藏凭据状态；基础设施失败仍必须显式失败。
3. 并发语义由行锁获取顺序决定，测试必须观察真实数据库竞争而非仅启动两个线程。
