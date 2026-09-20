# Task 5.3 — 原子 Refresh Token 轮换与重用拒绝

## 范围与阶段边界

承接 Task 5.2 验收后的继续指令，本任务实现
[roadmap Stage 5](../roadmap.md#stage-5--deferred-authentication-hardening)
中的内部原子轮换；完成后等待 owner 验收，不开始 Task 5.4，不 commit/push。
不修改数据库 schema、migration、依赖、配置、Compose、CI 或现有登录响应。
不增加 HTTP endpoint、cookie、logout、密码修改、token family 或自动重试。

## 固定契约

- 内部入口 `rotate_refresh_token(token: SecretStr, session)` 不接受调用方 user_id。
  旧凭据摘要查找是认证引导操作，不是客户端指定资源 ID 的访问；取得身份后，
  撤销 UPDATE 同时约束记录 ID、该记录的 user_id、未撤销及有效时间。
- 使用 Task 5.2 的严格格式和准确字节摘要，Repository 只接收摘要及可信元数据。
  通过 `SELECT ... FOR UPDATE` 锁定旧行，`populate_existing=True` 刷新 identity map，
  防止已有 Session 缓存绕过另一事务已经完成的撤销。
- 固定事务级 `SET LOCAL lock_timeout = '5s'` 限制锁等待；它不是整个请求超时。
  获取行锁之后才采样 UTC 时间，必须满足 `created_at <= now < expires_at` 且未撤销。
- 同一事务：锁定 → 时间/状态校验 → 条件撤销 → 生成替代凭据 → 摘要 insert/flush →
  commit → 交付内部 `RotatedRefreshToken(user_id, token, expires_at)`。
  新凭据有效期为轮换时刻起 7 天；旧行保留，不删除，不将原文写入数据库。
- Service 独占提交/回滚，Repository 不提交。调用者提供并关闭专用 Session，
  不得嵌套在其他写用例中，也不能调用会单独提交的 issuance 用例替代本事务。
- 格式错误、不存在、过期（含恰好到期）、未来创建、已撤销和重放均返回同一内部
  `InvalidRefreshTokenError`；生成/数据库失败返回固定 `RefreshTokenRotationError`。
  失败尝试 rollback，抑制可能泄露 SQL 参数的底层异常上下文；失败 Session 应关闭。
  不记录原文、摘要、数据库凭据或 SQL 参数，交付值 repr 隐藏令牌。
- 两个并发调用最多一个轮换成功，等待者读取提交后的撤销状态并拒绝旧凭据。
  重放不撤销已经交付的新凭据；token family/泄露检测策略不在本任务范围。

行锁与缓存刷新设计参考官方
[PostgreSQL locking](https://www.postgresql.org/docs/17/explicit-locking.html) 和
[SQLAlchemy populate_existing](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#populate-existing)。

## 文件与验收

- `app/core/exceptions.py`：安全拒绝及轮换失败错误。
- `app/repositories/refresh_tokens.py`：受限锁等待、锁定查询、带所有权的条件撤销。
- `app/services/refresh_tokens.py`：原子轮换、UTC/期限和安全交付。
- `tests/test_refresh_token_rotation.py`：19 项离线测试，覆盖 SQL 谓词、事务顺序、
  获取锁之后采样时间、重放/失效边界、生成/insert/commit/rollback 失败及保密。
- `tests/integration/test_refresh_token_rotation.py`：11 项真实 PostgreSQL 测试：
  成功及后继轮换、重放拒绝、其他用户不变、5 类非法凭据、撤销 UPDATE 后的
  随机源失败/摘要唯一性冲突/提交前故障回滚、陈旧 identity map、双 Session 实际锁竞争。
  并发测试用 `pg_blocking_pids` 证明发生数据库等待，不仅依赖线程同时开始。
- 更新本任务、roadmap、任务索引和 Task 5.2 验收状态，保留前置未提交工作。

验证顺序：定向离线 → 只启动 postgres-test → migration target guard →
upgrade/current/heads/check → 新增及完整 `integration and not external_provider` →
复查 guard/迁移 → lock、完整离线 pytest、Ruff lint/format、mypy、两项 diff 检查。
数据库 URL 仅在进程环境构造，不打印；JWT 只使用合成 test-only 值。
恢复测试容器原有状态，不操作 app、postgres-dev、开发卷，不调用外部 Provider。

## 当前状态与限制（2026-09-18）

Completed / 本地全部验证通过，owner 在验收报告后授权继续。
Task 5.4 进入 [HTTP 契约规划](stage-5-4-refresh-http.md)，尚未实现。

此前中断及恢复过程：

- 定向 `uv run pytest -q tests/test_refresh_tokens.py tests/test_refresh_token_repository.py tests/test_refresh_token_service.py tests/test_refresh_token_rotation.py`：退出 0，38 passed。
- mypy 初次退出 1：测试使用的 SQLAlchemy dialect 构造器缺少类型声明；改为通用
  statement compile 后复查退出 0，236 source files。定向测试复跑仍为 38 passed。
- Docker compose 状态查询退出 1：Linux engine pipe 不存在。启动 Docker Desktop
  的进程命令退出 0，但后台初始化仍失败；宿主日志明确报告
  `Docker/run/sailor-ingest.sock` 无法访问，不能视作引擎已启动。
- 上次中断时未启动/停止任何 Compose service，未执行数据库迁移或 integration。
  Owner 允许修复后再次检查，引擎已经可响应，三个服务均为 exited；无需清理 socket。
- 默认端口 5433 启动 postgres-test 退出 1：Windows 保留端口段 5355–5454 阻止绑定，
  未执行迁移。使用进程级 `STMS_POSTGRES_TEST_PORT=55433` 后重建并启动测试服务成功。
  没有修改 `.env`、Compose、系统保留端口或开发服务；只重建可丢弃的 tmpfs 测试库。
  未删除任何 socket、卷或开发数据库，没有恢复出厂设置。

最终验证（全部 `uv` / Docker 命令使用本机已安装的绝对可执行文件路径）：

| 检查 | 退出码 | 实际结果 |
| --- | --- | --- |
| 定向离线 pytest（上述四个文件） | 0 | 38 passed，恢复后复跑通过 |
| `docker compose up -d --wait postgres-test`（55433 override） | 0 | healthy；仅测试容器重建 |
| migration target guard（套件前/后） | 各 0 | 专用回环地址、stms_test、55433 |
| `uv run alembic upgrade head`（套件前/后） | 各 0 | 首次从空 tmpfs 完整升级，之后复查成功 |
| `uv run alembic current` / `uv run alembic heads`（套件前/后） | 各 0 | 唯一 head `86cd95365562` |
| `uv run alembic check`（套件前/后） | 各 0 | No new upgrade operations detected |
| `uv run pytest -m "integration and not external_provider" tests/integration/test_refresh_token_rotation.py --tb=short` | 0 | 11 passed |
| `uv run pytest -m "integration and not external_provider" tests/integration --tb=short` | 0 | 108 passed |
| `uv lock --check` | 0 | 98 packages，锁文件未修改 |
| `uv run pytest -q` | 0 | 982 passed，109 deselected |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 恢复后复跑 263 files already formatted（上次 262） |
| `uv run mypy app tests alembic` | 0 | 236 source files，无问题 |
| `git diff --check` / `git diff --cached --check` | 各 0 | 暂存区仍为空 |
| `docker compose stop postgres-test` | 0 | 恢复 exited；app、postgres-dev 始终 exited |

真实测试证明旧凭据重复使用被拒绝、替代凭据可以继续轮换、其他用户记录不变、
两个独立 Session 发生 PostgreSQL 锁等待后仅一个成功、已有 ORM 缓存不能绕过撤销。
撤销后随机生成失败、摘要唯一性冲突、flush 后提交前故障均回滚，旧凭据随后可重新轮换。
本轮没有修改实现代码或测试，补齐了此前受阻的真实验证并更新三份进度文档。

新增行和未跟踪文件的私钥、JWT、常见 Provider/GitHub 凭据、带口令 PostgreSQL URL
模式扫描均为 0 命中；人工检查本任务代码未发现敏感值日志或无关更改。
既有 Task 5.1/5.2 未提交实现保留，未 staging、commit、push 或运行远程 Actions。
额外 `docker desktop status` 诊断无响应后已中断（退出 1），没有用它判断容器状态。

提交确认丢失可能导致数据库已消费旧凭据而调用者没有收到替代凭据；不自动重试或
承诺此时旧凭据仍可用，调用者可能需重新登录。注入的 commit 故障发生在真实提交前，
不把此测试当作网络确认丢失的保证。Windows 默认测试端口冲突仍是环境限制；后续测试
可显式使用同一进程级端口 override，并让 URL 与迁移守卫保持一致。不声称已根治此前
Docker socket 故障，也未执行系统级修复。
HTTP 交付与错误状态映射留给 Task 5.4。

## 调用链与学习点

内部调用者 → `rotate_refresh_token` → 凭据格式/摘要 →
`get_by_hash_for_update` → UTC/状态校验 → `revoke_if_active` → 新凭据摘要 →
`create` → SQLAlchemy/PostgreSQL → commit → 安全内部交付值。本阶段没有 Router。

1. 行锁负责并发串行化，ORM 缓存刷新负责让校验读取到已提交的新状态。
2. 同一事务中撤销和插入才能一起成功或回滚；flush 不等同于 commit。
3. 原子数据库写入不能消除提交确认丢失，错误恢复契约必须诚实保留这一边界。
