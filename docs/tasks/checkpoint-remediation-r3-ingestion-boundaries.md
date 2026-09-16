# Checkpoint Remediation R3 — Ingestion boundaries

## 状态

- Planned
- 类型：Stage 11～Task 12.6 candidate checkpoint remediation
- 对应审查 Findings：P1 — multipart 在 `UploadFile` 前已接收完整 body；P2 —
  U+0000 通过解析但不能写入 PostgreSQL `text`
- 前置依赖：R2 完成并经 owner 验收
- 后续依赖：R4

## 根因

### 请求体边界

`upload_knowledge_document_endpoint` 收到 Starlette `UploadFile` 后才从 `file.file` 读取
`MAX_DOCUMENT_BYTES + 1`。在 FastAPI 调用 endpoint 之前，multipart parser 已从 ASGI
`receive` 消费请求并可能把文件 spool 到临时存储。因此现有检查能限制传给 Service 的
文件内容，却不能限制请求入口的网络、内存和临时磁盘消耗。

当前应用没有 request-body middleware。现有超限 API 测试由 TestClient 一次性构造完整
body，只证明最终 413 和 endpoint 内容检查，没有证明无 `Content-Length`、多 ASGI
chunk 或 multipart parser 前的停止行为。

### PostgreSQL 文本边界

`_display_name`、`_normalize_text` 和 `_normalize_pdf_page` 处理路径、换行、空白与长度，
但没有拒绝 U+0000。TXT/Markdown 解码或 PDF 提取结果可以保留该字符；最终
`display_name`/`extracted_text` 写入 PostgreSQL `varchar/text` 时失败。此错误不属于当前
路由映射的 `KnowledgeDocumentInvalidError`，会成为未受控数据库失败，而不是 422。

## 目标

在 multipart parser 之前对知识文档上传请求实施确定性、流式、总字节上限，同时保留
endpoint/Service 的 5 MiB 文件内容防线。所有将进入 PostgreSQL 字符类型的上传派生文本
在 Repository 调用前拒绝 U+0000，并以既有安全错误契约返回 422。

## 当前代码基线

- 上传入口：`POST /api/v1/knowledge/documents`。
- 文件内容上限：`app/services/knowledge_documents.py::MAX_DOCUMENT_BYTES`，5 MiB。
- 允许一个名为 `file` 的 multipart 文件，类型为 txt/md/pdf；文件名上限 255 字符。
- 文本上限 200,000 字符；PDF 最多 100 页。
- `app/main.py::create_app` 当前只安装 validation exception handler 和 router。
- `KnowledgeDocument` 持久化字符串入口包括 `display_name`、allowlisted `media_type`、
  SHA-256 文本和 `extracted_text`；其中用户/解析器可影响的是 display name 和 extracted
  text。
- 当前没有通用 `app/middleware/` 目录；新增路径必须在实现时确认命名。

## 范围

1. 为知识上传路径增加纯 ASGI 请求体限制器，直接包装 `receive`，累计每个
   `http.request.body` chunk 的实际字节数。
2. 同时处理有/无 `Content-Length`：明显超限可在读取前 413；缺失、错误或 chunked 时
   以实际累计字节为准。
3. 限制的是完整 multipart body，不是仅文件 bytes。总上限由 5 MiB 文件预算加一个固定
   multipart envelope 预算组成。
4. 固定 envelope 预算暂定 64 KiB，必须在实现前用允许的最长文件名、合法 content-type、
   RFC 合法 boundary 和单文件 multipart 编码证明足够；若证据要求调整，只能调整一个
   有明确测试的常量，不能无限放大。
5. 对超过总上限的流立即停止向下游交付并返回既有安全 413 detail；不得先把完整 body
   存入 bytes、临时文件或 `Request.body()`。
6. endpoint 仍读取至 `MAX_DOCUMENT_BYTES + 1`，Service 仍验证 `len(content)`，形成入口、
   HTTP 文件内容和领域解析三层防线。
7. 在解析结果进入 Repository 前对 `display_name` 和最终 `extracted_text` 拒绝 U+0000；
   PDF 每页结果最终必须经过同一持久化文本验证。
8. NUL 失败复用 `KnowledgeDocumentInvalidError`，路由返回固定 422，不回显文件名、文本、
   parser 或数据库诊断。

## 非目标

- 不依赖 Nginx、Traefik、云网关或真实反向代理才能通过测试。
- 不移除 endpoint/Service 文件大小检查，不把 5 MiB 改成 multipart 总大小。
- 不缓存或重组完整请求 body，不使用 `BaseHTTPMiddleware`/`Request.body()` 预读实现。
- 不宣称 antivirus、恶意 PDF 沙箱、压缩炸弹/OCR 防护或生产 DoS 全面解决。
- 不扩大文件类型、页数、文本长度，不增加对象存储或后台解析。
- 不修改数据库列或历史 migration；U+0000 应在应用边界拒绝。
- 不对所有无 body 的路由施加不必要开销；限制器至少按 method/path 精确限定上传入口。

## 预计修改文件

- `app/main.py`
- `app/api/v1/endpoints/knowledge_documents.py`
- `app/services/knowledge_documents.py`
- `tests/test_knowledge_document_api.py`
- `tests/test_knowledge_document_service.py`
- `tests/integration/test_knowledge_documents.py`
- `docs/security-and-limitations.md`
- `README.md`（只校准上传上限与 413/422 说明）
- `docs/roadmap.md`（R3 完成后记录结果）

候选新增文件：

- `app/middleware/request_body_limit.py`（当前无 middleware package；实现时确认后创建）
- `app/middleware/__init__.py`
- `tests/test_request_body_limit.py`

## 数据流或状态转换

```text
ASGI server
  -> pure ASGI upload-body limiter
       Content-Length > total envelope limit -> 413, endpoint not called
       streamed cumulative bytes > limit    -> 413, stop downstream receive
       within limit                         -> multipart parser
  -> UploadFile
  -> endpoint reads at most 5 MiB + 1
       file bytes > 5 MiB -> 413
  -> Service parses and validates persisted text
       U+0000 in display_name/extracted_text -> 422 before Repository
  -> Repository -> SQLAlchemy -> PostgreSQL
```

限制器不得改变非目标路由的 scope、receive 或 send。若下游响应已开始后才出现超限，
实现必须 fail closed 并由测试证明不会发送第二个响应；正常 multipart parse 阶段应在响应
开始前检测到超限。

## ASGI 接收流限制设计

- 实现形式：`async __call__(scope, receive, send)` 的纯 ASGI middleware，不继承
  `BaseHTTPMiddleware`。
- 选择条件：`scope["type"] == "http"`、`method == "POST"`、path 精确匹配知识上传。
- 早期检查：安全解析单个 `Content-Length`；负数、非整数、重复冲突值按无可信长度处理，
  最终由真实流计数决定，不能据此绕过。
- 流式检查：包装 `receive`；每次收到 `http.request` 累加 `len(body)`，在把超限 chunk
  交给 multipart parser 前抛出内部专用异常。
- 响应：middleware 在最外层捕获专用异常，返回固定 JSON 413；异常不得包含长度、header、
  boundary、文件名或 body。
- 内存：只保存整数计数和当前 ASGI message，不拼接 chunks、不调用 `Request.body()`。
- 合法边界：总上限至少允许一个恰好 5 MiB 的合法文件，加最长允许文件名、content
  disposition/content type、边界及结束符。额外字段不是合法 endpoint payload，可在总上限
  内由 FastAPI schema 处理，超出则 413。
- Chunked：自建 ASGI receive 序列，用多个 `more_body=True` 消息验证，无需依赖某个 HTTP
  客户端是否真的发送 HTTP/1.1 chunked framing。

## 实施步骤

1. 建立单文件 multipart 的最小、最大合法 envelope 测量测试，固定并解释预算常量。
2. 实现与 FastAPI 无关的纯 ASGI limiter 和专用内部异常/413 responder。
3. 在 `create_app` 中按确定顺序安装 middleware；验证 exception handler 和路由行为不变。
4. 增加直接 ASGI 测试：Content-Length 早拒绝、无长度流式超限、多 chunk、恰好上限、
   下游未调用/未收到超限 chunk、非目标路由透明。
5. 保留并回归 endpoint/Service 的 5 MiB 文件内容检查，加入恰好 5 MiB 合法文件测试。
6. 增加共享持久化文本验证，覆盖文件名、TXT、Markdown、PDF page/final text 的 U+0000。
7. 通过公开 API + real PostgreSQL 证明 U+0000 返回 422、没有插入行且 Session 可继续用。
8. 更新限制文档，运行质量门、差异和秘密检查后停止。

## 最小测试

```powershell
uv run pytest -q tests/test_request_body_limit.py tests/test_knowledge_document_api.py tests/test_knowledge_document_service.py
```

至少覆盖：

- `Content-Length` 大于总上限时 endpoint/multipart parser 不执行；
- 没有 `Content-Length` 的多 chunk stream 在第一个超限 chunk 处返回 413；
- chunked body 与单 chunk body 使用同一累计规则；
- 恰好 5 MiB 的合法 file part 不因固定 multipart 开销误报 413；
- 5 MiB + 1 文件即便总 body 尚在 envelope 上限内，仍被 endpoint/Service 拒绝；
- 413 body 固定、无输入回显，并只有一个 response start；
- 文件名、TXT/MD 文本和 PDF 提取文本中的 U+0000 均为 422；
- 非目标 HTTP 路由和普通 validation handler 不变。

## PostgreSQL、Docker 与集成测试要求

- ASGI 总字节限制使用普通直接 ASGI 测试，不依赖 Docker、代理或网络服务器。
- U+0000 必须使用 guarded `postgres-test` 走公开 API，证明在 flush/commit 前被转换成
  422 且数据库无记录。
- 执行现有知识文档 integration，证明合法 txt/md/pdf、所有权、事务和迁移行为未回归。
- 不需要新 migration；仍运行 `alembic current/heads/check` 验证无 schema drift。

## 最终质量门

```powershell
uv lock --check
uv run pytest -q
docker compose up -d --wait postgres-test
uv run pytest -m "integration and not external_provider" tests/integration/test_knowledge_documents.py
uv run alembic current
uv run alembic heads
uv run alembic check
docker compose stop postgres-test
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
git diff --check
git diff --cached --check
```

## 客观验收标准

- [ ] 入口总 body 在 multipart parser 前按实际 receive bytes 被限制。
- [ ] 无 Content-Length、错误长度和多 chunk 流不能绕过限制。
- [ ] 实现不缓存/拼接整个请求体，超限 chunk 不交付下游。
- [ ] 合法恰好 5 MiB 文件及最大合法 multipart metadata 可进入 endpoint。
- [ ] endpoint 和 Service 的 5 MiB+1 内容检查继续有效并返回固定 413。
- [ ] display name、TXT/MD 和 PDF 最终提取文本中的 U+0000 在 Repository 前被拒绝。
- [ ] U+0000 API 结果为固定 422，PostgreSQL 无行、无原文或数据库诊断泄露。
- [ ] 无新 migration、反向代理依赖、文件类型或功能扩张。
- [ ] 最小、PostgreSQL integration 和最终质量门通过。

## 风险和回滚

- 风险：middleware 顺序或异常处理错误会产生双响应/500；直接 ASGI send 记录必须覆盖。
- 风险：envelope 过小误拒合法 5 MiB 文件，过大削弱入口保护；常量必须由最大合法编码
  测试支撑。
- 风险：只信任 Content-Length 会被 chunked/错误 header 绕过；真实 stream 计数是最终
  权威。
- 风险：PDF parser 可能返回其他 PostgreSQL 不接受的编码边界；本任务只修复已证明的
  U+0000，不宣称完整文档安全。
- 回滚：独立撤销 limiter、文本校验、相关测试和文档；不更改 migration 或候选基线。

## 停止条件

- Starlette/FastAPI 生命周期使纯 ASGI 包装无法在 response start 前安全返回 413；
- 合法 multipart 最大 envelope 无法在固定、小范围预算内定义；
- 需要反向代理、对象存储、流式 parser 替换或新生产依赖；
- 修复需要数据库列/migration 变化而原因不能限定在本任务；
- PostgreSQL 测试目标不安全或会触碰开发库；
- 候选暂存区被取消暂存、commit 或 push。

## 完成报告格式

- 修改文件；
- ASGI receive 数据流、总上限和 envelope 预算依据；
- 无长度/chunked/合法 5 MiB/超限测试证据；
- U+0000 的所有检查入口、HTTP 和 PostgreSQL 结果；
- migration 未变化证明；
- 最小、integration、最终质量门；
- 残余 parser/DoS 风险、调用链和三个学习点；
- 明确停止，未开始 R4 或后续产品阶段。
