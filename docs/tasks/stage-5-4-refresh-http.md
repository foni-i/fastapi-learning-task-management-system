# Task 5.4 — Refresh HTTP 与双令牌交付契约

## 状态与前置条件

Completed / Owner 已确认 JSON body 及登录响应扩展；代码、离线与真实 PostgreSQL
验证均通过。Owner 在验收报告后要求继续下一任务，现由 Task 5.5 单独记录后续工作。
预计确认后约 1～2 个专注小时，完成本任务后停止，不开始 Task 5.5，不 commit/push。

前置实现：Task 5.1 摘要表与迁移、Task 5.2 内部签发、Task 5.3 原子轮换已完成。
规划时登录只返回 `access_token`、`token_type`，没有公开 refresh endpoint；
校验错误仅遮蔽 password。现在代码已按下述契约扩展，实际验证证据见末尾记录。

## 已确认方案：仅 JSON body

本项目当前是后端 API，建议本阶段选择 JSON body，不同时支持 Cookie 或两套输入来源。
这是一项项目取舍，不是声称 JSON 对所有浏览器场景更安全。

- `/api/v1/auth/login` 仍接受原有 email/password JSON；成功响应增加刷新凭据和期限。
- 新增 `POST /api/v1/auth/refresh`，只从 JSON body 接受 `refresh_token`，不依赖有效
  Access Token，不接受调用方 user_id。Cookie、query 或 Authorization 都不能替代 body。
- 不设置 Cookie，不增加 CORS 凭据策略、CSRF 系统或前端存储实现。浏览器持久登录若
  改用 HttpOnly Cookie，必须另行确认 Secure/SameSite/Path、CSRF 和本地 HTTPS 契约。
- 正式部署使用 HTTPS；受信任客户端负责保护收到的令牌，不将其写入 URL、日志、
  演示输出或提交文件。不推荐浏览器 localStorage 保存会话凭据。
- 登录新增字段是显式契约变更：读取旧字段的客户端可继续使用，严格校验恰好两个字段
  的客户端需要调整。登录从只读变为需要成功写入刷新摘要，数据库写失败不能返回部分成功。

Cookie/客户端存储取舍参考
[OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)。

## HTTP 契约

| 接口/结果 | 状态 | 字段或行为 |
| --- | --- | --- |
| login 成功 | 200 | `access_token`、`token_type="bearer"`、`refresh_token`、`refresh_expires_at` |
| refresh 成功 | 200 | 同上；替代刷新凭据有效期为轮换时刻起 7 天 |
| login 凭据不匹配 | 401 | 保留现有固定 detail 和 Bearer challenge，不暴露账户是否存在 |
| refresh 格式合法但不存在/过期/撤销/重放 | 401 | 固定 `detail="Refresh token is invalid"`；不区分原因 |
| JSON/schema 无效 | 422 | 有界、无敏感输入的校验信息；不得触发签发/轮换 |
| 签名配置、签发或数据库故障 | 503 | 固定安全 detail，不包含底层异常或 SQL；不交付任何令牌 |

请求 schema：唯一字段 `refresh_token`，使用 SecretStr，严格 43 个 ASCII URL-safe
字符，不 trim、不改变大小写；拒绝额外字段、null、错误类型和无效长度/字符。
输入模型启用 `extra="forbid"` 与隐藏错误输入，但不能仅依赖模型设置实现 HTTP 脱敏。

成功交付是返回完整令牌的唯一预期出口，不能错误地把 SecretStr 的掩码当作可用令牌。
公开响应严格只含表中四字段，禁止返回 owner ID、ORM、摘要、撤销状态或内部时间。
`refresh_expires_at` 是带时区 UTC ISO 8601；Access Token 沿用当前签名、声明与有效期。
内部返回值和 response model repr 隐藏令牌；测试覆盖显式 HTTP 序列化和日志不泄露。

login/refresh 的成功与已处理的 401/422/503 响应均设置
`Cache-Control: no-store`、`Pragma: no-cache`。禁止把响应缓存当作凭据存储。
参考 [RFC 6749 §5.1](https://www.rfc-editor.org/rfc/rfc6749#section-5.1) 的令牌响应缓存规则；
本项目仍是第一方自定义认证 API，不宣称实现完整 OAuth2 协议。

## 原子性与分层

Router 负责 schema、状态码、响应与缓存头；Service 负责身份、令牌对构造及事务。
Repository 保持无 HTTP 概念、无 commit/rollback。Session 由依赖创建并最终关闭。

- 登录：查用户/验密 → 构造并暂存刷新摘要 → 生成 Access JWT 与交付值 → 单次 commit →
  返回。失败 rollback，不能遗留已提交的刷新凭据或返回仅 Access Token 的降级成功。
- 刷新：严格输入 → 摘要锁定旧行 → 锁后时间/状态校验 → 条件撤销和替代摘要插入 →
  生成 Access JWT 与交付值 → 单次 commit → 返回。身份只来自锁定的旧凭据所属用户。
- 不能简单调用现有会 commit 的 `rotate_refresh_token` 后再签 JWT；否则 JWT 签发失败
  已经消费旧凭据。实现时将必要的无提交准备步骤提取为私有共享函数，由最外层用例
  持有单一 commit/rollback，保留 Task 5.2/5.3 公共内部用例和已有测试的行为。
- 现有旧行重放拒绝、固定锁等待上限、缓存刷新和 UTC 边界保持不变。
- 签名/构造/insert/提交前故障必须回滚且旧刷新凭据仍可用；不自动重试。
  提交确认丢失或提交后 HTTP 连接中断仍可能需要重新登录，不能承诺网络级原子交付。

## 脱敏与边界

对这两个认证入口的 422 响应采用安全字段白名单，只保留有界的固定字段位置、错误类别
和固定安全消息；不回传 input、ctx、原请求片段、任意额外字段名或完整敏感 payload。
覆盖嵌套类型错误、非对象 body、无效 JSON、额外字段和把凭据放在字段名中的情况。
保持其他路由当前错误契约不变，不借此开展全应用错误框架重构。
日志不得包含密码、完整令牌、摘要、Authorization、Cookie 或原始数据库异常。

暂不增加 schema migration、依赖、配置项、清理任务、token family、denylist、OAuth
Provider、退出或密码修改。限制每个令牌字段不等于实现全局流式请求体上限；不在本任务
暗示已完成全面 DoS 防护或生产认证加固。未实现的限流等风险仍保留在安全文档中。

## 预期改动与验收

- `app/api/v1/endpoints/auth.py`：扩展登录响应、增加 refresh、固定异常映射与缓存头。
- `app/schemas/auth.py`：刷新请求、双令牌响应及有界错误 schema。
- `app/services/authentication.py`、`app/services/refresh_tokens.py`：单事务双令牌编排。
- `app/core/exceptions.py`、`app/main.py`：必要的安全错误和认证入口校验脱敏。
- 现有认证/刷新单元及 API 测试，新增 refresh API 和真实 PostgreSQL HTTP 测试。
- README/API 示例、架构与安全现状文档仅更新本任务相关内容；示例只用明显占位符。
- 成功闭环：注册/登录 → Access 调用 users/me → refresh → 新 Access 调用 users/me →
  旧 Refresh 重放被拒绝；过期 Access 不阻止持有有效 Refresh 的刷新。
- 覆盖 A/B 身份隔离、错误类型/长度/额外身份字段、签名与存储故障回滚、两次并发刷新
  仅一个成功、刷新后旧 Access 仍按原 TTL 有效、日志/repr/错误/OpenAPI 不泄露。
- 保留 Task 5.1～5.3 全部回归；不把 fake Session 测试当作真实事务和 HTTP 闭环证明。

验证：最小相关 schema/service/API 测试 → 仅 postgres-test → migration target guard →
upgrade/current/heads/check → 新增与完整 `integration and not external_provider` →
复查迁移 → `uv lock --check`、完整 pytest、Ruff lint/format、mypy app tests alembic、
两项 git diff 检查及敏感信息审查。只在进程设置测试 URL 与合成 test-only JWT secret。
Windows 若仍保留 5433，使用已验证的进程级端口 55433，同时对齐 URL/Compose/守卫。
恢复测试服务原状态，保留开发卷与全部既有未暂存修改。

## 学习重点与本轮结果

实际代码调用链：login Router → authenticate_user → UserRepository/验密 →
`_prepare_issuance` → Access JWT/TokenPairResponse → commit → HTTP 四字段交付。
refresh Router → refresh_authentication → `_prepare_rotation` → 摘要锁定/条件撤销/
替代摘要 flush → Access JWT/TokenPairResponse → commit → HTTP 四字段交付。
CredentialRoute 仅包围 login/refresh，捕获依赖及未预期异常为安全 503；JSON 编码解析
失败映射为固定 422。应用级校验 handler 对这两个入口最多输出三项白名单信息。
HTTP 缓存头和错误转换不进入 Repository；原有独立签发/轮换用例继续持有自身事务。

1. 凭据交付的 JSON/Cookie 选择决定客户端存储和 CSRF 边界。
2. 新 Access JWT 签发必须纳入旧 Refresh 消费的成功/失败边界。
3. SecretStr 隐藏 repr 不等于 HTTP 校验错误已脱敏，也不等于成功响应可以返回掩码。

## 实施与验证记录（2026-09-18）

实际修改上述六个应用文件（含既有未跟踪 refresh service）、登录/API/模型公开契约测试、
integration 认证 fixture 的精确 RefreshToken 清理、主路由清单，新增
`tests/test_refresh_api.py` 和 `tests/integration/test_refresh_http.py`。
README、架构、需求、安全限制、roadmap 和任务索引同步更新，并在恢复验证后更新状态。
需求中的“响应不泄露令牌”澄清为普通资源/错误/日志不泄露，成功认证交付是唯一例外。

| 命令/检查 | 退出码 | 真实结果 |
| --- | --- | --- |
| 定向 pytest：test_refresh_api、test_main、test_login_api、test_authentication_service、test_auth_schemas、test_refresh_tokens、test_refresh_token_model、test_refresh_token_service、test_refresh_token_rotation | 0 | 97 passed |
| `uv lock --check` | 0 | 98 packages，未修改依赖 |
| `uv run pytest -q --tb=line` | 0 | 1017 passed，120 deselected |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 266 files already formatted |
| `uv run mypy app tests alembic` | 0 | 238 source files |
| `git diff --check` / `git diff --cached --check` | 各 0 | 暂存区仍为空 |
| `docker compose ps -a` | 首次 1；用户重启后 0 | 原始及恢复后的服务状态均为 exited |
| `docker compose up -d --wait postgres-test` | 0 | 55433 进程级 override；测试服务 healthy |
| migration target guard（套件前/后） | 各 0 | 回环地址、专用 stms_test、测试端口 55433 |
| `uv run alembic upgrade head`（套件前/后） | 各 0 | 首次从空 tmpfs 升级，套件后复查成功 |
| `uv run alembic current` / `uv run alembic heads`（套件前/后） | 各 0 | 唯一 head `86cd95365562` |
| `uv run alembic check`（套件前/后） | 各 0 | No new upgrade operations detected |
| `uv run pytest -m "integration and not external_provider" tests/integration/test_refresh_http.py tests/integration/test_authentication.py tests/integration/test_refresh_token_issuance.py tests/integration/test_refresh_token_rotation.py --tb=line` | 0 | 31 passed，其中新增 HTTP 11 passed |
| `uv run pytest -m "integration and not external_provider" tests/integration --tb=line` | 0 | 119 passed，无真实 Provider |
| `docker compose stop postgres-test` | 0 | 恢复 exited；app、postgres-dev 始终 exited |

首次回归退出 1，发现旧两字段/只读事务断言和新增路由清单需调整；路由包装器最初按
未包含完整前缀的 self.path 判断，已改用请求路径。新测试使用不被项目接受的 .test 邮箱、
Settings 类型声明不接受 _env_file 的问题也已修正，最终全部离线质量门通过。
已有 Task 5.2/5.3 定向用例回归通过；不将历史 integration 成绩替代本次验证。

Docker 正常启动命令退出 0 但引擎没有恢复；日志仍显示
`C:/Users/xu/AppData/Local/Docker/run/sailor-ingest.sock` 无法访问。按 owner 已授权的
单 socket 修复范围，核实进程路径后停止 Docker 进程并尝试改名保留；Rename-Item 退出 1。
只读 fsutil query 报 Error 1920，随后非递归单文件 Remove-Item 也退出 1。
两次尝试的 finally 均重新启动 Desktop；没有文件被改名或删除，没有创建备份。
未执行 fsutil delete、目录清理、WSL 重置、卷删除或开发数据库操作。

用户随后重启 Docker；引擎恢复后补齐真实验证，没有继续修改或删除 socket。
新增 PostgreSQL HTTP 文件的 11 项用例全部通过：双用户闭环、过期/其他用户 Access
不影响 Refresh 身份、旧 Access 仍有效、三类失效状态、login/refresh 各三类提交前
故障回滚与重试、双 HTTP 请求竞争仅一个成功。完整 integration 119 passed，定向离线
97 passed，完整离线 1017 passed（120 deselected），上表质量门在恢复后再次执行通过。
本次恢复轮次没有修改应用或测试；只更新 README、架构、安全限制、roadmap、任务索引
与本任务的验证状态。既有代码/测试指纹一致，暂存区仍为空；差异及未跟踪文件的有界
敏感信息扫描无命中，未发现无关修改。没有新增 migration 或依赖，唯一 head 经真实
数据库核验仍为 `86cd95365562`，无 schema 漂移。

仅操作原本停止的 postgres-test 并恢复停止；未操作开发容器/卷，未修改 `.env`、Compose、
系统保留端口或 Docker 配置。继续保留提交确认丢失/HTTP 响应丢失可能需重新登录的限制；
本次恢复不等于根治 Docker socket 故障，Windows 测试端口冲突仍可能需要进程级 override。
不 commit/push，不运行远程 Actions，不开始 Task 5.5；现在等待 owner 验收。
