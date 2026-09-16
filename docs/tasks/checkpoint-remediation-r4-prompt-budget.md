# Checkpoint Remediation R4 — Deterministic prompt budget

## 状态

- Planned
- 类型：Stage 11～Task 12.6 candidate checkpoint remediation
- 对应审查 Finding：P2 — 合法 Project/Task/RAG 数据可使最终
  `VersionedPrompt.input` 超过 20,000 字符
- 前置依赖：R3 完成并经 owner 验收
- 后续依赖：R5

## 根因

`build_agent_plan_proposal_prompt` 将完整 goal、analysis、第一页 20 个 Projects、第一页
20 个 Tasks 和 approval feedback 序列化，再附加最多 12,000 字符 grounding，最后才让
`VersionedPrompt.input(max_length=20_000)` 校验。各来源分别合法并不代表组合后合法；其中
Task description 单项可达 5,000 字符，goal/analysis 还重复包含 objective/constraints。

现有 grounding 测试只断言 grounding 自身不超过 12,000 字符，没有调用最终 prompt
builder。prompt 测试使用空的 Project/Task page。因而四个合法的长 Task description
即可在 Provider 调用前触发 Pydantic `string_too_long`。

## 目标

在最终 JSON 序列化之前为每类输入定义确定性预算和投影策略，以最终
`VersionedPrompt.input` 字符数为硬约束。优先保留目标、记录身份/状态、最新 Project/
Task 和相关度最高的 knowledge；任何裁剪都通过 prompt 内的安全 metadata 明确表示。
20,000 字符 Schema 上限保持不变。

## 当前代码基线

- `VersionedPrompt.input` 最大 20,000 字符。
- `PlanningGoal.objective` 最大 2,000；最多 20 个 500 字符 constraints。
- context 固定读取 Project/Task 第一页各 20 项；Repository 既有顺序是最新优先并使用 ID
  稳定 tie-breaker。
- Project description 最大 2,000；Task description 最大 5,000。
- grounding 已按 hybrid retrieval 相关度排序，最多 10 条、每 excerpt 500 字符，rendered
  上限 12,000。
- approval feedback 最大 1,000。
- prompt JSON 使用 `ensure_ascii=False`，与 Pydantic 字符长度语义一致。

## 范围

1. 建立一个最终 input 20,000 字符的代码常量，并将预算集中在 prompt 模块，避免测试与
   实现出现第二套数字。
2. 固定初始预算表；实现可在不超过各项上限的前提下把未使用额度按确定顺序回收，但
   任何时候最终硬上限不变：

| 分区 | 初始最大字符 | 保留策略 |
| --- | ---: | --- |
| 固定 framing、JSON 标点和 truncation manifest | 1,000 | 必须完整保留 |
| goal + 去重后的 analysis | 4,000 | objective 优先，constraints 按输入顺序 |
| revision feedback | 1,100 | 完整优先，最多现有 1,000 输入字符 |
| Projects projection | 3,000 | 当前列表顺序，身份/名称/状态/日期优先 |
| Tasks projection | 5,000 | 当前列表顺序，身份/title/状态/日期优先 |
| grounding | 5,000 | retrieval 排名顺序，citation identity 优先 |
| 最终安全余量 | 900 | 不分配；吸收编码/framing 漂移 |

3. 不再在 goal 和 analysis 中重复序列化相同 objective/constraints；analysis 只保留其新增
   的确定性字段，如 required context。
4. Project/Task 每项先保留结构化关键字段，再裁剪 description；若仍超预算，按既有稳定
   顺序保留能完整容纳的条目，并记录省略数量。
5. grounding 使用相同 final-budget API 生成，按已排名 evidence 顺序保留 citation ID、
   source/page/ordinal/ranks 后裁剪 excerpt；不能保留 excerpt 却丢失 citation identity。
6. 截断按 Python Unicode 字符而不是 UTF-8 bytes 执行；向前调整截断点，避免把 combining
   mark、variation selector 或 ZWJ 序列留成断裂尾部，不引入新的 Unicode 依赖。
7. prompt 加入有界 `truncation` manifest，至少标识 goal constraints、Project/Task
   descriptions/items、grounding excerpts/items 是否被裁剪及省略数。
8. builder 返回前对最终实际字符串再次断言 `<= 20_000`；预算算法失配应使用固定安全
   内部错误，不能把完整 prompt 放进异常。

## 非目标

- 不提高或移除 20,000 字符 Schema 上限。
- 不减少数据库字段本身的公开/API 上限，不改变 Project/Task 列表 API。
- 不添加模型摘要调用、第二 Provider call、tokenizer 依赖或动态 token 价格逻辑。
- 不更改 hybrid retrieval 排名、citation 验证、approval fingerprint 或 owner filtering。
- 不把完整 context 移出边界后通过其他字段偷偷发送给 Provider。
- 不改变 `build_study_plan_prompt`，除非共享的安全截断 helper 能证明必要且无语义变化。

## 预计修改文件

- `app/agent/prompts.py`
- `app/agent/grounding.py`
- `tests/test_agent_prompts.py`
- `tests/test_agent_grounding.py`
- `tests/test_agent_planning_nodes.py`
- `tests/test_agent_graph.py`
- `docs/architecture.md`（只补充最终 prompt budget 数据流）
- `docs/security-and-limitations.md`
- `docs/roadmap.md`（R4 完成后记录结果）

候选新增文件：`app/agent/prompt_budget.py`。只有当投影/Unicode/预算代码会使
`prompts.py` 失去单一职责时才拆分；否则保留在现有模块。

## 数据流或状态转换

```text
validated goal + analysis
latest-first Project/Task page
relevance-ranked grounding
optional revision feedback
  -> deterministic public-field projection
  -> per-part character budgets
  -> Unicode-safe truncation + manifest
  -> compact JSON/framing serialization
  -> final len(input) <= 20,000 assertion
  -> VersionedPrompt -> ProviderRequest
```

同一输入必须生成 byte-for-byte 相同 prompt。裁剪不能改变列表次序、citation ID、owner
边界或 approval revision 的含义。

## 实施步骤

1. 先加入复现测试：4 个最大合法 Task description、多个最大 Project、Unicode、最大
   goal/constraints、最大 feedback 和接近上限 grounding 同时输入，确认当前 builder
   失败。
2. 定义集中预算常量、投影字段 allowlist、截断 marker 和 truncation manifest schema。
3. 实现 Unicode 安全前缀裁剪；覆盖 CJK、emoji、combining mark、variation selector、
   ZWJ 和无空格长字符串。
4. 实现 Project/Task 投影：关键字段完整优先、description 次优先、条目按现有顺序，
   稳定记录 omitted/truncated。
5. 让 grounding renderer 接受调用者预算或新增有界投影入口；保留独立 12,000 防线，但
   最终 prompt 使用更小的分区预算。
6. 构造 compact payload，回收未使用预算时使用固定顺序：goal → tasks → projects →
   grounding；保留 900 字符硬余量。
7. 在最终构造点校验实际字符数与 manifest 一致，错误不回显内容。
8. 运行 planning/graph/approval fingerprint 回归，更新架构与限制文档并停止。

## 最小测试

```powershell
uv run pytest -q tests/test_agent_prompts.py tests/test_agent_grounding.py tests/test_agent_planning_nodes.py
```

测试至少包含：

- 所有字段最小时不发生截断且语义与当前输出等价；
- 多个最大合法 Project/Task description 不抛 ValidationError；
- goal/constraints、feedback、20 个长 Tasks、20 个长 Projects 和 grounding 同时接近上限；
- 最终 input 始终 `<= 20_000`，并保留安全余量；
- 相同输入完全确定，改变排序/字段会产生可解释变化；
- 最新 Project/Task 和最高排名 citation 优先保留；
- manifest 准确标识每个被截断/省略分区；
- CJK、emoji、combining/variation/ZWJ 输入保持有效且没有断裂尾部；
- prompt/error/trace 中不泄露被省略的完整内容；
- citation identity 与最终 approval fingerprint 仍覆盖实际发送的 proposal。

## PostgreSQL、Docker 与集成测试要求

本任务算法可用普通测试完成，不需要 schema 或 migration。增加一个使用真实 Pydantic
PublicProject/PublicTask 最大合法字段构建 context 的集成式普通测试；不以手工 dict 绕过
Schema。

如需验证 Repository 列表顺序，复用现有 PostgreSQL Project/Task integration，不新增
数据库写路径。Docker 不是本任务必需门；最终全 integration 可留给 checkpoint 独立
复审，但必须运行现有 Agent graph/recovery 普通回归。

## 最终质量门

```powershell
uv lock --check
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests alembic
git diff --check
git diff --cached --check
```

## 客观验收标准

- [ ] 20,000 字符上限未提高，最终实际序列化 input 永不超限。
- [ ] 固定 framing、goal/analysis、feedback、Projects、Tasks、grounding 和余量分别有代码
  常量及测试。
- [ ] 合法最大字段和多个长 Tasks 不再在 Provider 调用前触发 `string_too_long`。
- [ ] 截断稳定、Unicode 安全，并在 prompt manifest 中明确可见。
- [ ] 当前 latest-first Project/Task 和 relevance-ranked evidence 优先级被保留。
- [ ] citation identity、owner boundary、approval 和 Provider schema 没有削弱。
- [ ] 没有额外模型调用、tokenizer/Unicode 依赖、schema/migration 或上限规避。
- [ ] 最小测试、Agent 回归和最终质量门通过。

## 风险和回滚

- 风险：裁剪改变模型质量。通过固定优先级、显式 manifest、版本化 prompt 和 eval 回归
  观察；如 prompt 语义有实质变化，应升级 prompt version 并更新精确测试。
- 风险：JSON escaping 使预算估算偏大。最终预算必须针对 `json.dumps` 后字符串，不能用
  原始字段长度代替。
- 风险：Unicode grapheme 处理不完整。标准库实现需由组合字符和 emoji 序列测试证明；
  若不能可靠满足，停止而不是按 bytes 截断。
- 风险：描述裁剪丢失关键信息。关键结构字段优先，manifest 明示，R6 再衡量行为影响。
- 回滚：独立撤销 R4 的 prompt/grounding 投影、测试和文档；不得改变数据库或其他整改。

## 停止条件

- 预算只能通过提高 20,000 上限、删除安全 framing 或省略所有 grounding 达成；
- 需要模型摘要、tokenizer、外部服务或新依赖；
- 无法为 Unicode 截断建立确定性测试；
- prompt version 是否必须升级会破坏现有 checkpoint 且需要 owner 决策；
- 修复要求改变 Project/Task API、RAG 排名或 approval fingerprint 契约；
- 候选暂存区被取消暂存、commit 或 push。

## 完成报告格式

- 修改文件；
- 最终预算表、回收顺序和 truncation manifest；
- 最大合法 context 的实际字符数和确定性证据；
- Unicode、排序、citation 和 approval 回归结果；
- prompt version 是否变化及依据；
- 最小测试和最终质量门；
- 残余模型质量风险、数据流和三个学习点；
- 明确停止，未开始 R5 或 Task 12.7。
