# ADR 006 — 文献子系统(外部检索作为 Harness 工具)

状态:已采纳(Phase 4')
日期:2026-08-04

## 背景

本系统的首要职责是研究辅助:从 arXiv、GitHub、Nature(Springer Nature
API)与 Science 系元数据(CrossRef)抓取文献,以其为依据生成回答,并
可选地把 PDF 灌入私有 WeKnora 知识库。Phase 2 已确立 `KnowledgeProvider`
作为*内部*知识子系统的边界(ADR 004)。外部文献源在本质上不同:它们是
第三方的、无认证或轻认证的、有限速的、返回元数据(而非 chunk),且带有
版权约束。

## 决策

1. **外部检索是 Harness 工具,不是 KnowledgeProvider。**
   文献源实现 `LiteratureSource`(`literature/base.py`),只经由形式化
   的 `ToolRegistry`(`literature_search`,风险 L1)暴露给图。
   `KnowledgeProvider` 仍是 WeKnora 的唯一边界;外部源除通过工具的
   小记录契约外,绝不写图 state。

2. **默认仅元数据(版权边界)。**
   `LiteratureRecord` 携带标题/作者/摘要(≤800 字符)/URL/doi/出处/
   发表时间 —— 绝不含全文。仅当任务显式设置 `ingest_external=true` 时,
   才经 `literature_ingest` 工具(风险 L2,每任务 ≤2 个 PDF,每个 ≤25MB,
   校验 content-type,SSRF 主机白名单,重定向逐跳重验)下载并灌入
   WeKnora。

3. **外部源的降级策略。**
   外部检索是可选增强。失败的源被跳过并上报(工具结果中的 `errors`
   列表、`external_degraded` SSE 事件);其余源照常贡献。与 ADR 004
   的内部策略不同,外部源上的认证错误**不会**让任务失败 —— 配置错误的
   可选源不得阻塞研究。工具整体失败降级为仅内部回答并设置
   `external_error`。

4. **图分支,不是循环。**
   `retrieve_internal → decide_external → retrieve_external →
   ingest_external → generate_grounded_answer`。分支仅在
   `allow_external=true` 且内部命中 < 3 时触发。每个任务至多一轮外部
   检索(`retrieval_rounds`);Phase 4' 不做迭代式再检索。

5. **引用覆盖两种证据。**
   引用门同时接受内部 `chunk_id` 与外部 `source_id`
   (`{source_type}:{external_id}`)。引用其他任何东西的 claim 一律
   丢弃并计数,与之前相同。

6. **ToolRegistry 形式化(文档 §13)。**
   `ToolSpec` 携带版本/描述/风险/超时/max_retries/requires_approval/
   幂等策略/输入输出 schema;`ToolResult` 把每次调用包装为
   call_id/status/error_code/duration_ms/retryable。重试仅针对
   `TransientToolError`/超时;handler 错误不重试。风险门控与审批门控
   按调用强制。

## 影响

- 新增外部源 = 新增一个 `LiteratureSource` + 一行注册;图与引用门无需
  改动。
- arXiv 限速(3 秒)在 `ArxivSource` 客户端侧强制。
- Nature 源仅在设置了 `PK_AGENT_SPRINGER_NATURE_API_KEY` 时注册;否则
  静默缺席(不是错误)。
- 全文灌库按任务显式开启,默认流程保持仅元数据、版权安全。
