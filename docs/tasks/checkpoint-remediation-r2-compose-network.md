# Checkpoint Remediation R2 — Compose network boundary

## 状态

- Planned
- 类型：Stage 11～Task 12.6 candidate checkpoint remediation
- 对应审查 Finding：P1 — Compose 默认把 app、postgres-dev、postgres-test 发布到
  `0.0.0.0`/`[::]`
- 前置依赖：R1 完成并经 owner 验收
- 后续依赖：R3

## 根因

`compose.yaml` 的三个端口使用短语法 `${PORT}:container_port`。Compose 在没有 host IP
时默认绑定所有宿主接口。开发数据库同时使用仓库可见的本地默认凭据和持久 named
volume，因此“只供本机开发”的文档意图没有由网络配置强制执行。

`tests/test_docker_assets.py` 读取原始 YAML 字符串，只确认端口变量和容器内部数据库
主机名，没有解析 Compose model，也没有检查容器运行时的 `HostIp`。README 说明本地
用途和服务隔离，但没有准确说明当前对局域网接口的暴露。

## 目标

让 app、postgres-dev 和 postgres-test 的默认 host publish 明确绑定
`127.0.0.1`，同时保留 `STMS_APP_PORT`、`STMS_POSTGRES_DEV_PORT` 和
`STMS_POSTGRES_TEST_PORT`。任何远程暴露都必须通过额外、显式的 Compose override，
不能由默认文件或一个含糊的默认环境值悄然启用。

## 当前代码基线

- `compose.yaml` 定义 app、持久化 postgres-dev 和 tmpfs postgres-test。
- app 在容器内监听 `0.0.0.0:8000`，这是容器内部可达性要求，不等同于宿主机发布范围。
- app 通过 Compose 内部 DNS 使用 `postgres-dev:5432`，不需要数据库发布到所有网卡。
- 当前实际测试容器曾显示 `0.0.0.0:5433->5432` 和 `[::]:5433->5432`。
- `tests/test_docker_assets.py` 是当前 Docker/Compose 静态契约入口。
- README 同时记录本机 uv、Compose、开发/测试数据库与端口变量。

## 范围

1. 将三个默认端口映射改为带 host IP 的 Compose 语法：
   `127.0.0.1:${PORT}:container_port`。
2. 保留现有三个端口环境变量及默认端口，不更改容器端口、服务名、数据库名或存储方式。
3. 测试必须执行 `docker compose config` 并检查解析后的 host IP，不能只搜索原始文本。
4. 运行容器并通过 Docker/Compose inspect 证明所有 published port 的 HostIp 是
   `127.0.0.1`，不存在 `0.0.0.0` 或 `::`。
5. 更新 README，使“本机可达”“Compose 内部可达”和“远程暴露需要显式 override”三者
   表述一致。
6. 远程暴露不进入默认 Compose。若未来确有需要，操作者必须提供额外 `-f` override，
   并自行配置防火墙、TLS 和非示例凭据。

## 非目标

- 不把 app 容器内部的 Uvicorn bind 改成 `127.0.0.1`；那会破坏容器端口转发。
- 不删除现有 host port 能力，不改变端口环境变量。
- 不新增 reverse proxy、TLS、云网络、VPN、Kubernetes 或生产 secret manager。
- 不提供默认启用的远程访问文件或 `0.0.0.0` fallback。
- 不改变 postgres-dev volume、postgres-test tmpfs、健康检查或数据库安全守卫。
- 不重建、清空、downgrade 或删除开发数据库/volume。

## 预计修改文件

- `compose.yaml`
- `tests/test_docker_assets.py`
- `README.md`
- `docs/security-and-limitations.md`
- `docs/roadmap.md`（R2 完成后追加真实验收状态）

若测试需要结构化解析辅助，候选新增路径为 `tests/helpers/compose.py`；优先在现有测试中
用 `subprocess` 调用 `docker compose config --format json` 并解析 JSON，避免新增运行时
依赖。当前任务不计划提交远程访问 override 文件。

## 数据流或状态转换

```text
host browser/process -> 127.0.0.1:${STMS_APP_PORT} -> app:8000
host dev client       -> 127.0.0.1:${STMS_POSTGRES_DEV_PORT} -> postgres-dev:5432
host test client      -> 127.0.0.1:${STMS_POSTGRES_TEST_PORT} -> postgres-test:5432
app container         -> Compose network DNS postgres-dev:5432
remote peer           -X-> default published ports
```

远程发布只能由显式附加文件把 `host_ip` 改为非 loopback；默认 `compose.yaml` 不提供该边。

## 实施步骤

1. 用 `docker compose config --format json` 保存当前解析结构的有界证据，确认三项 host IP
   缺失/全接口行为。
2. 仅修改三个 `ports` 映射，显式加入 `127.0.0.1`；不改容器内部监听。
3. 把 Docker 资产测试从关键字符串检查扩展为解析后的 service/target/published/host_ip
   契约；保留 non-root、secret 和存储隔离断言。
4. 更新 README 与安全限制文档，解释端口变量只改变端口、不改变默认 loopback bind。
5. 解析使用默认端口和三个非默认端口的 Compose 配置，证明覆盖能力未丢失。
6. 只启动验证所需服务，检查运行时端口绑定；app 验证可复用开发库但不得重建或清理
   volume，数据库测试优先使用 disposable postgres-test。
7. 停止本任务启动的容器，运行最终质量门并审查不存在全接口发布。

## 最小测试

```powershell
uv run pytest -q tests/test_docker_assets.py
docker compose config --quiet
docker compose config --format json
```

测试对每个服务断言：

- `host_ip == "127.0.0.1"`；
- target 分别为 8000/5432/5432；
- published port 来自现有环境变量或默认值；
- 不存在 `0.0.0.0`、空 host IP 或 IPv6 任意地址；
- app 的数据库 URL 仍使用内部 `postgres-dev:5432`。

## PostgreSQL、Docker 与集成测试要求

```powershell
docker compose config --quiet
docker compose up -d --wait postgres-test
docker compose ps
docker inspect <postgres-test-container-id>
docker compose stop postgres-test
```

运行时必须从 Docker port bindings 断言 `HostIp=127.0.0.1`。随后以明确临时端口覆盖
`STMS_POSTGRES_TEST_PORT` 再解析/启动一次，证明端口变量仍有效且 bind 不变。

app 的实际验证使用 `docker compose up -d --build --wait app` 时，不得重建、downgrade
或清除 postgres-dev；验证 live/ready 后只停止本任务启动的 app。若无法确定开发服务
现状，停止 app 运行检查，以解析结果和 postgres-test 运行检查为最低证据，并报告缺口。

## 最终质量门

```powershell
uv lock --check
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
docker compose config --quiet
git diff --check
git diff --cached --check
```

## 客观验收标准

- [ ] app、postgres-dev、postgres-test 默认解析 host IP 均为 `127.0.0.1`。
- [ ] 运行时 inspect 不出现 `0.0.0.0`、空 HostIp 或 `[::]` 的 published port。
- [ ] 三个现有端口环境变量仍可改变 published port，且不会改变 loopback bind。
- [ ] app 容器内部仍监听 8000，且通过内部 DNS 连接 postgres-dev。
- [ ] README 和安全限制文档准确说明本机/容器/远程网络边界。
- [ ] 默认仓库不含自动启用的远程暴露配置。
- [ ] postgres-dev 持久卷、postgres-test tmpfs 和 test URL 安全守卫保持不变。
- [ ] 解析配置、实际运行绑定、最小测试和最终质量门全部通过。

## 风险和回滚

- 风险：Windows/WSL/Docker Desktop 对 host IP 的呈现可能与 Linux 略有不同。验收同时
  检查解析 model 和实际 inspect，并记录平台版本。
- 风险：把容器内部 Uvicorn bind 一并改为 loopback 会导致 health/port forwarding
  失败；测试必须区分内部监听和宿主发布。
- 风险：既有用户依赖局域网访问。该访问原本没有安全承诺；整改后必须由用户显式维护
  override，而不是放宽默认值。
- 回滚：只撤销 R2 的 Compose、测试和说明文档修改；不得回退其他任务或删除 volume。

## 停止条件

- Docker Compose 版本无法提供可解析的 host IP 或运行时绑定证据；
- 现有端口变量无法在不恢复全接口 bind 的情况下保留；
- 需要访问、重建、清空或 downgrade 开发数据库；
- 用户要求交付生产远程访问、TLS 或公网部署设计；
- 修改涉及应用功能、数据库 schema、Provider 或 Task 12.7；
- 候选暂存区被重置、取消暂存、commit 或 push。

## 完成报告格式

- 修改文件；
- 默认/覆盖端口的解析后 host bind 表；
- 实际运行时 Docker port binding 证据；
- README 与 Compose 一致性说明；
- 启停的服务及开发 volume 保留情况；
- 最小测试和最终质量门；
- 未解决风险、调用链和三个学习点；
- 明确停止，未开始 R3 或 Task 12.7。
