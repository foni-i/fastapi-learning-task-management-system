# Task 5.6 — 修改密码与全部 Refresh Token 吊销

## 状态与任务边界

Completed / 本地完整验证通过，owner 在验收报告后确认继续 Task 5.7。Docker 阻塞已在 owner 重启后解除；
最终定向及完整 integration、迁移复查和全部质量门通过。Owner 在 Task 5.5 验收报告后要求继续，仅推进 roadmap Task 5.6。
不开始 Task 5.7，不 commit/push，不增加找回密码、管理员、JWT denylist 或新依赖/迁移。
保留现有未暂存修改；开始时 43 个状态条目、暂存区为空。

## HTTP 契约

- `POST /api/v1/users/me/change-password` 要求有效 Bearer Access Token；用户 ID
  只取认证依赖解析的用户，不接受客户端 user_id、邮箱、目标用户或哈希。
- JSON 仅包含 `current_password` 和 `new_password`，均使用 SecretStr，不 trim/
  casefold。当前密码长度 1～128，新密码复用注册规则：12～128 Unicode 字符、
  不可全空白；新旧值相同拒绝，额外字段拒绝。错误输入返回有界脱敏 422。
- 在锁后验证当前密码；认证失效或密码不匹配为固定 401 + Bearer challenge。
  存储、哈希或依赖故障为固定安全 503，不返回密码、哈希、SQL 或底层诊断。
- 成功空 204。所有成功与已处理的 401/422/503 都 no-store/no-cache。
  不自动签发新令牌、不设置 Cookie；修改后用新密码重新登录。
- 全部现有未撤销 Refresh Token（包括过期或异常未来时间记录）原子标记撤销，
  已撤销记录的时间保持不变；其他用户不受影响。不删除历史记录。
- Access JWT 不立即失效，仍按原 TTL 有效。持有 Access JWT 但不知道当前密码
  不能改密。没有限流、密码找回、全局 session version 或强制 Access 下线。

## 事务与并发设计

Router/SecretStr schema → Bearer/current-user dependency → `change_password` →
`UserRepository.get_by_id_for_update` → 当前密码验证 → Argon2id 新摘要 →
用户密码/updated_at flush → `RefreshTokenRepository.revoke_all_for_owner` →
单次 commit → 204；任何提交前失败 rollback 两部分写入，Repository 不提交。
不能使用依赖先前读取的 password_hash：锁查询使用 populate_existing 获取最新状态。

为了避免“改密吊销完成，旧登录/刷新又提交一个有效令牌”的竞态，本任务必要地调整
现有凭据操作的锁顺序：用户行 → 刷新凭据行，固定 5 秒锁等待上限。

- 登录按规范化邮箱锁定用户后再验密；登录先完成，其新刷新记录会被改密吊销；
  改密先完成，等待的旧密码登录被拒绝，新密码登录可成功。
- 刷新/退出先以摘要定位 owner 并锁用户，然后按摘要及 owner 锁定凭据并刷新缓存。
  刷新先完成，其后继也被改密吊销；改密先完成，旧凭据刷新被拒绝。
- Refresh Repository.create 也锁用户，覆盖既有内部签发路径；它不替调用方认证，
  内部用例仍要求可信已认证身份，不能暴露为按 user_id 任意签发接口。
- 两次旧密码改密串行化后只有一次成功。不同用户锁不同记录，不引入全局锁。
- 批量吊销只含 owner 和 revoked_at IS NULL 条件。撤销时间取数据库
  greatest(当前 UTC, created_at)，避免时钟倒退违反已有约束。

锁住用户期间包含密码哈希工作，意味着同用户凭据操作可能等待或超时 503；这是本阶段
清晰事务边界的取舍，不代表已做吞吐量/抗暴力破解加固。统一顺序避免引入用户行与
令牌行之间的反向锁依赖，不把旧 refresh 锁竞争回归测试替换为仅 mock 证明。

提交确认或响应丢失时，新密码可能已生效；旧密码重试可能 401，应尝试新密码登录。
不承诺网络级原子交付。改密不是幂等操作，不能照搬 logout 的固定 204 重试契约。

## 验收与修改范围

- 新增 password-change schema/service/endpoint；扩展既有安全错误边界。
- User Repository 增加锁查询与密码更新；Refresh Repository 增加 owner 吊销及
  统一锁顺序；登录在锁内验密。没有模型、迁移、配置或依赖变化。
- 单元/API：正确事务顺序、错误密码/缺失用户/哈希/flush/吊销/commit/rollback
  失败、长度/空白/同密码/额外字段、身份来源、debug 脱敏、no-store、OpenAPI。
- 真实 PostgreSQL：旧密码失效、新密码可登录、全部会话/轮换后继撤销、另一用户
  不受影响、旧 Access 仍有效、两部分失败一起回滚、陈旧 identity-map 更新；
  用 pg_blocking_pids 验证改密与登录/刷新/改密的真实竞争及两个先后顺序。
- 最小测试 → 仅 postgres-test（进程级 55433）→ migration guard → Alembic
  upgrade/current/heads/check → 定向与完整 integration（排除 external_provider）→
  迁移复查 → 完整离线/lock/Ruff/mypy/diff/敏感检查 → 测试服务恢复原停止状态。

## 执行记录

### 2026-09-18 实施，2026-09-19 中断后恢复检查

修改应用：`app/api/v1/endpoints/auth.py`、`users.py`、`app/main.py`、
`app/repositories/users.py`、`refresh_tokens.py`、`app/schemas/user.py`、
`app/services/authentication.py`；新增 `app/services/password_change.py`。
新增 `tests/test_password_change.py`，扩展 `tests/integration/test_refresh_http.py`；
调整 `test_authentication_service`、`test_refresh_api`、`test_refresh_token_rotation`、
`test_main` 的锁查询/路由契约。同步 README、architecture、requirements、security、
roadmap、tasks 索引和 Task 5.5 状态。没有改动迁移、模型、依赖、CI、Compose 或 .env。

| 命令/检查 | 退出码 | 真实结果 |
| --- | --- | --- |
| 最小离线 pytest：password_change、authentication_service、refresh_api、refresh_token_rotation、refresh_token_service、logout、main | 0 | 129 passed；新增改密离线 33 项 |
| 初次 `docker compose ps -a` | 0 | 三个服务原均 exited |
| 初次 `docker compose up -d --wait postgres-test` | 0 | 进程级端口 55433，healthy |
| 初次 migration target guard | 0 | 专用回环测试目标通过 |
| 初次 `uv run alembic upgrade head` | 0 | 空 tmpfs 升级至 head |
| 初次 `uv run alembic current` / `heads` | 各 0 | 唯一 head `86cd95365562` |
| 初次 `uv run alembic check` | 0 | No new upgrade operations detected |
| `uv run pytest -m "integration and not external_provider" tests/integration/test_refresh_http.py tests/integration/test_refresh_token_rotation.py tests/integration/test_refresh_token_issuance.py --tb=line` | 0 | 54 passed：HTTP 39（18 项新增改密）、rotation 11、issuance 4 |
| 初次完整 integration | 未取回 | 工具会话因中断丢失，不能据此声称通过 |
| 恢复后 `docker compose ps -a` / `up -d --wait postgres-test` | 各 1 | dockerDesktopLinuxEngine 管道不存在，未进入迁移/integration |
| 正常启动 Docker Desktop（Start-Process Hidden） | 0 | 仅表示进程已启动，引擎未恢复 |
| 启动后 Docker server version / `compose ps -a` | 各 1 | 引擎管道仍不存在 |
| 最终 `uv lock --check` | 0 | 98 packages |
| 最终 `uv run pytest -q --tb=line` | 0 | 1078 passed，150 deselected |
| 最终 `uv run ruff check .` | 0 | All checks passed |
| 最终 `uv run ruff format --check .` | 0 | 271 files already formatted |
| 最终 `uv run mypy app tests alembic` | 0 | 241 source files |
| `git diff --check` / `git diff --cached --check` | 各 0 | 暂存区仍为空 |
| 已修改新增行/暂存新增行/未跟踪文件有界敏感模式扫描 | 0 | 完整 JWT、带凭据数据库 URL、私钥、常见 Provider/GitHub key 模式无命中 |

54 项定向真实验证后补强了其他用户密码隔离测试，增加已过期刷新记录和无刷新记录两项
边界；最终离线回归已重新运行，最后这部分真实数据库测试和完整 integration 尚待补跑。
自动整理本轮文件的 import/format 后质量门通过，没有用离线用例代替真实事务证明。

只读检查显示 Docker backend 日志在 2026-09-19 再次出现 initializing Ingest server、
sailor-ingest.sock 和 The file cannot be accessed by the system 特征。没有输出完整日志，
没有停止/删除进程、清理 socket、恢复出厂、删除卷或重置 WSL。正常启动后进程存在，
引擎仍不可访问；最终容器状态及测试服务恢复停止尚无法核实，不能宣称已经恢复。
app/postgres-dev 未被本任务显式启动或重建，开发卷未操作。

### 当时待恢复的验收步骤（现已执行，结果见下节）

Owner 恢复 Docker 引擎后，先检查容器状态，继续使用进程级 55433 与专用测试 URL，
只启动 postgres-test；重新运行 target guard 和四项 Alembic 检查，再运行上述定向测试
与完整 `uv run pytest -m "integration and not external_provider" tests/integration --tb=line`。
最后复查迁移、恢复 postgres-test 停止、检查最终差异。测试 JWT secret 仅采用进程级
明显 test-only 的合成值，不输出 URL/密码/令牌。不从历史成绩推断本轮结果。

既有未暂存修改和空 index 保留；未 stage/commit/push，未运行远程 Actions。
保留 Access JWT 不立即失效、锁等待/限流不足和提交确认丢失风险。Task 5.7 不开始；
当时等待 Docker 恢复，未将该轮未完成验证视为通过。

### 2026-09-19 owner 重启 Docker 后的最终验证

恢复时 `compose ps -a` 退出 0：app 与 postgres-dev 仍 exited，postgres-test 为
Exited (255)。只启动 postgres-test，进程级端口仍为 55433。没有修改 Compose、.env、
系统端口保留规则或 Docker 配置，没有清理 socket/卷。代码、测试、迁移文件在恢复前后
SHA-256 指纹一致；本轮仅更新 README、roadmap、tasks 索引及本任务的验证状态。

| 命令/检查 | 退出码 | 真实结果 |
| --- | --- | --- |
| `uv run pytest -q tests/test_password_change.py tests/test_authentication_service.py tests/test_refresh_api.py tests/test_refresh_token_rotation.py tests/test_refresh_token_service.py tests/test_logout.py tests/test_main.py --tb=line` | 0 | 129 passed |
| `docker compose up -d --wait postgres-test` | 0 | 测试服务 healthy |
| migration target guard（套件前/后） | 各 0 | 专用回环 stms_test、端口 55433 |
| `uv run alembic upgrade head`（前/后） | 各 0 | 首次从空测试 tmpfs 升级，套件后复查成功 |
| `uv run alembic current` / `uv run alembic heads`（前/后） | 各 0 | 唯一 head `86cd95365562` |
| `uv run alembic check`（前/后） | 各 0 | 无 schema 漂移 |
| `uv run pytest -m "integration and not external_provider" tests/integration/test_refresh_http.py tests/integration/test_refresh_token_rotation.py tests/integration/test_refresh_token_issuance.py --tb=line` | 0 | 56 passed：HTTP 41（其中改密 20）、rotation 11、issuance 4 |
| `uv run pytest -m "integration and not external_provider" tests/integration --tb=line` | 0 | 149 passed，无真实 Provider |
| `uv lock --check` | 0 | 98 packages |
| `uv run pytest -q --tb=line` | 0 | 1078 passed，150 deselected |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 271 files already formatted |
| `uv run mypy app tests alembic` | 0 | 241 source files |
| `git diff --check` / `git diff --cached --check` | 各 0 | 既有未暂存修改保留，暂存区为空 |
| 有界敏感模式扫描 | 0 | 已修改新增行、暂存新增行、未跟踪文件均无命中 |
| `docker compose stop postgres-test` / `docker compose ps -a` | 各 0 | postgres-test 恢复 Exited (0)，app/postgres-dev 保持原状态 |

最后补充的无刷新记录、过期记录及其他用户密码隔离用例已实际通过；未使用旧结果替代
本轮证明。工作区仍为 49 个已有修改/未跟踪条目，未 stage/commit/push，没有运行远程
GitHub Actions。Task 5.6 的本地验证已补齐，现在等待 owner 验收，不开始 Task 5.7。
剩余限制仍包括 Access JWT 原 TTL、锁内哈希的等待成本、缺少限流、提交确认丢失；
Docker socket 故障不宣称已被本任务根治，后续本地验证仍可能需要测试端口 override。

## 三个学习点

1. 密码更新和全刷新凭据吊销必须共享同一事务，不能各自提交。
2. 只做批量 UPDATE 不足以阻止并发新签发；相关流程必须遵守同一锁顺序。
3. Access JWT 与数据库刷新会话的生命周期不同；改密不能被描述为立即撤销全部 JWT。
