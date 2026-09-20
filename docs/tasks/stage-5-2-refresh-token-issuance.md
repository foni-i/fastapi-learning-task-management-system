# Task 5.2 — 随机 Refresh Token 签发、摘要存储与事务

## 范围与决策

Owner 已验收 Task 5.1 并授权继续。本任务对应
[roadmap Stage 5](../roadmap.md#stage-5--deferred-authentication-hardening)
的 Task 5.2，预计 1～2 个专注小时；完成后停止等待验收。

- 为后续认证编排提供内部 `issue_refresh_token(trusted_user_id, session)` 用例。
  身份必须由已认证调用方提供；本函数不是认证入口，不接受 HTTP/Agent 自选用户。
- 使用标准库 `secrets.token_urlsafe(32)`，显式 32 随机字节，输出 43 个 URL-safe 字符。
  原文用 `SecretStr` 包装；SHA-256 对准确的 UTF-8 字节计算，不修剪或转换大小写。
- 摘要函数只接受 43 个 ASCII 字母、数字、下划线或短横线；非法格式抛固定安全错误。
  此处是输入格式校验，不是验证某个令牌已被签发、仍有效或归某用户所有。
- 采用固定 7 天有效期。本阶段不增加可调配置，未来若调整生命周期需明确验收。
  同一个 timezone-aware UTC 时刻用于 `created_at` 和期限计算；测试允许注入受控时间。
- Repository 只接收 `user_id`、摘要和时间，执行 add/flush，不 commit/rollback。
  不添加查询、轮换、撤销、清理或额外 migration。
- Service 拥有本次写事务：生成 → 摘要 → insert/flush → commit → 返回。
  内部返回值只含 `SecretStr token` 和 `expires_at`，repr 隐藏令牌；不返回 ORM/摘要。
- 每个失败路径尝试 rollback，并以 `RefreshTokenIssuanceError` 的固定消息失败关闭；
  包括唯一性冲突、外键失败、commit 和 rollback 异常，不记录原始错误/SQL/凭据。
  不自动重试；调用者必须关闭失败 Session，不能把本用例嵌入另一个写事务。
- commit 报错不一定意味着数据库未提交，例如提交确认丢失。本用例保证这种情况下
  不交付原文，不承诺消除所有孤立摘要；后续到期仍适用。本轮注入的 commit 故障明确
  发生在数据库 commit 前，以验证可确定的回滚路径。

## 接口与阶段边界

登录仍只签发 Access Token；新用例尚未挂接任何 Router、Agent Tool 或登录 Service。
HTTP body/cookie、登录响应兼容性与交付策略在 Task 5.4 的接口契约中统一确定。
Task 5.3 再实现原子轮换和旧令牌重用拒绝；Tasks 5.5/5.6 负责退出及密码修改后的撤销。
不修改 Task 5.1 的表结构、已验收迁移、依赖、Compose 或 CI，不 commit/push。

## 文件与验证

- `app/core/refresh_tokens.py`：安全随机源与准确字节摘要。
- `app/core/exceptions.py`：固定安全签发错误。
- `app/repositories/refresh_tokens.py`：仅摘要写入。
- `app/services/refresh_tokens.py`：内部返回值、7 天期限与事务边界。
- `tests/test_refresh_tokens.py`、`tests/test_refresh_token_repository.py`、
  `tests/test_refresh_token_service.py`：随机字节数量、格式、大小写敏感摘要、UTC、
  原文不进入 Repository、事务顺序及各失败路径。
- `tests/integration/test_refresh_token_issuance.py`：独立 Session 读取已提交行，两个用户
  归属、跨用户摘要碰撞、缺失用户、flush 后提交前故障、恢复写入及 SQL/日志不泄露。
- 更新 roadmap、任务索引及 Task 5.1 验收状态，保留全部既有未暂存修改。

先运行上述三个离线测试，然后只启动原本停止的 `postgres-test`。从 Compose 的测试
配置在进程内构造 URL，使用合成 test-only JWT secret，不打印敏感值。迁移命令前调用
既有 `validate_migration_test_target`，运行 upgrade/current/heads/check；迁移期间暂时将
`STMS_DATABASE_URL` 指向测试 URL，integration 前恢复原环境，避免安全门拒绝相同目标。
运行新增和完整 `integration and not external_provider` 套件，再复查 guard/head/drift，
最后执行 lock、完整离线 pytest、Ruff lint/format、mypy、两项 Git diff 检查。
结束时恢复测试容器原状态，不操作开发库或开发卷。

## 学习重点

实际调用链：可信内部调用方 → `issue_refresh_token` → 安全随机/摘要函数 →
`RefreshTokenRepository.create` → ORM → PostgreSQL → commit → 内部安全交付值。

1. 随机令牌的熵与其摘要的存储格式各自承担什么职责。
2. flush 能触发约束检查，只有 commit 成功后才能交付可用凭据。
3. SecretStr/repr 隐藏是防误泄露措施，不是加密，也不能替代谨慎的日志和 HTTP 设计。

## 状态

Completed / 验收后 owner 授权继续（2026-09-18）。下述为 2026-09-17 的本地实现结果；
Task 5.3 的进展独立记录在 [轮换任务](stage-5-3-refresh-token-rotation.md)，不混入本次签发验收。

## 实际验收结果

| 检查 | 退出码 | 结果 |
| --- | --- | --- |
| `uv run pytest -q tests/test_refresh_tokens.py tests/test_refresh_token_repository.py tests/test_refresh_token_service.py` | 0 | 19 passed |
| `docker compose up -d --wait postgres-test` | 0 | healthy；原始状态为 exited |
| migration target guard | 0 | 专用回环端口 5433、stms_test；迁移前及完整套件后通过 |
| `uv run alembic upgrade head` | 0 | 首次从空 tmpfs 建库；套件后复查成功 |
| `uv run alembic current` / `uv run alembic heads` | 各 0 | 唯一 head 仍为 `86cd95365562`，未新增 revision |
| `uv run alembic check` | 0 | 首次及套件后均无新 upgrade operations |
| `uv run pytest -m "integration and not external_provider" tests/integration/test_refresh_token_issuance.py` | 0 | 4 passed |
| `uv run pytest -m "integration and not external_provider" tests/integration` | 0 | 97 passed |
| `uv lock --check` | 0 | 98 packages，未修改依赖/锁文件 |
| `uv run pytest -q` | 0 | 963 passed，98 deselected |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 260 files already formatted |
| `uv run mypy app tests alembic` | 0 | 234 source files，无问题 |
| `git diff --check` / `git diff --cached --check` | 各 0 | 暂存区仍为空 |
| `docker compose stop postgres-test` | 0 | 恢复 exited；app、postgres-dev 仍为 exited |

以上 `uv` / Docker 使用本机已安装的绝对可执行文件路径。中间 lint 和 mypy 检查各曾退出
1：将 rollback 的 try/pass 改为 `suppress`、绑定循环中的测试令牌、修正标准库 monkeypatch
引用及 Session 子类测试变量类型后均通过。定向和完整功能测试没有失败。

真实测试证明：成功签发可被独立 Session 读取、SQL/参数只携带摘要、提交前失败无残留行、
摘要碰撞不会覆盖其他用户记录、缺失用户被外键拒绝、失败后以新 Session 可继续签发。
所有失败返回固定安全错误，日志及 repr 不包含原文或摘要；不宣称已实现公开 refresh
认证、轮换并发控制或 HTTP 交付。

保留 Task 5.1 的全部既有实现；仅更新其 owner 验收状态和相关任务索引，新增本任务代码/
测试/记录。差异审查未发现真实凭据、完整 Token 或无关修改。未操作开发库、开发卷，
未调用真实 Provider，未 commit/push 或运行本次改动的远程 Actions。所有修改保持未暂存。
当前无阻塞项；提交确认丢失的边界见上文，HTTP 和后续生命周期能力仍待各自任务。
