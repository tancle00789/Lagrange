# Phase 04' 构建报告 —— 文献子系统

日期:2026-08-04

## 交付范围

Phase 4' 被重排到文档原 Phase 4/5 之前(见 `pk_agent_roadmap_resequenced`
决策):文献抓取是这个科研副驾驶的两大核心职能之一。

- **ToolRegistry 形式化**(`harness/registry.py`,文档 §13):完整
  `ToolSpec`(版本/描述/风险/超时/max_retries/requires_approval/
  幂等策略/schema)、统一 `ToolResult`(call_id/status/error_code/
  duration_ms/retryable)、按调用的风险 + 审批门控、超时强制、重试仅限
  瞬时错误。删除了陈旧的重复 `PromptAssembler`。
- **文献子系统**(`literature/`):`LiteratureRecord`(仅元数据,摘要
  ≤800 字符),四个真实源 —— arXiv(经 defusedxml 解析 Atom XML,
  客户端 3 秒限速)、GitHub 仓库检索(可选 token)、CrossRef(Science
  系元数据,polite-pool mailto)、Springer Nature(仅在配置了 API key
  时注册)。共享 `SourceHttpClient` 把所有失败映射为类型化错误,仅对
  连接错误/5xx 重试。
- **灌库管道**(`literature/ingest.py`):经 SSRF 校验的 PDF 下载(仅
  https、主机白名单、手动跟随重定向且逐跳重验、25MB 上限、content-type
  检查)→ WeKnora 灌库。仅在显式 `ingest_external=true` 时运行(ADR 006
  版权边界),每任务 ≤2 个 PDF。
- **图分支**:`retrieve_internal → decide_external → retrieve_external
  → ingest_external → generate_grounded_answer → citation_gate → finalize`。
  外部分支在 `allow_external=true` 且内部命中 < 3 时触发。按 DOI/URL
  去重,上限 8 个源。引用门现在同时接受内部 chunk_id 与外部 source_id;
  合成兜底覆盖仅外部证据的情况。新增 SSE 事件:`external_retrieved` /
  `external_degraded` / `external_ingested`。
- **API/前端**:`POST /api/v1/literature/search`(直接源扇出,不灌库);
  任务创建/状态携带 `allow_external` / `ingest_external` /
  `external_sources`;SPA 有两个显式开关复选框与外部来源面板(标题
  链接、作者、出处/年份、摘要、已灌库徽标);报告引用有效性覆盖
  source_id。

## 验证(全部为真实验证,本机)

| 关卡 | 结果 |
|---|---|
| 从 app 容器可达外部 API | arxiv 200,github 200,crossref 200,springer 401(端点可达,需要 key) |
| `ruff check src tests` | ok(为 S314 增加 defusedxml) |
| `mypy src` | ok,45 个文件 |
| `pytest` | 67 passed(registry 门控/超时/重试,四个源全部经 MockTransport 覆盖,灌库 SSRF/大小/重定向用例,图分支/去重/引用门/降级,API 含文献端点 + 外部任务) |
| `npm run build`(前端) | ok |
| `docker compose up -d --build app` | ok |
| `scripts/smoke_test.sh` | ok |
| `scripts/smoke_test_literature.sh` | ok —— 真实 arXiv 预检,带 `allow_external` 的任务抓到 8 个真实外部源(arxiv+crossref+github),报告有据可依,全部引用有效 |
| `scripts/smoke_test_weknora.sh`(真实 qwen3:4b + WeKnora) | ok —— 图改动后回归确认 |

## 未做 / 注意事项

- Springer Nature 源未对真实 API 测试(没有 key);默认不注册。其响应
  映射仅有 MockTransport 测试覆盖。
- GitHub 源检索的是仓库,不是代码/papers-with-code;star 与 topic 落在
  `extra` 中。
- 外部证据仅以元数据+摘要进入 prompt;纯外部任务的回答质量取决于摘要
  的信息量。
- `literature_ingest` 由任务级开关做 L2 门控,而不是按调用的 HITL 中断;
  工具级审批中断仍是未来工作(威胁模型已知缺口)。
- arXiv 限速是进程级的;并发 app 副本会各自独立限速。
