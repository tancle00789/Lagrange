# 阶段 12 构建报告 — Skill 系统（Phase 7）

Date: 2026-08-17
ADR: `docs/adr/014-skill-system.md`

## 范围

路线图 Phase 7（docx §14）：从已完成的任务中提炼可复用流程（"skills"），离线且确定性地评估它们，并仅让人工审批通过的 skills 影响后续运行 —— 完成最后一项主要路线图事项，并与 AgentOS skill 概念对齐。

## 交付内容

### skills 模块 (`src/pk_agent/skills/`)
- `models.py` — `SkillRecord`（semver 版本化，`extra="forbid"`，校验 skill_id/procedure/regexes）、`TriggerCondition`、`EvalCase`、`EvalResult`、`SkillCandidate`；`ALLOWED_TRANSITIONS` 状态机（candidate → testing → approved → deprecated）。
- `store.py` — `SkillStore` 协议；`PgSkillStore`（幂等 DDL，`skill_versions` + `skill_eval_runs`，`SELECT ... FOR UPDATE` 状态流转，approve 在一个事务中取代旧版 approved 版本）以及用于开发/测试的 `InMemorySkillStore`。
- `registry.py` — 每个 family 最新 approved 版本的运行时视图；显式 load/refresh，确定性关键词/正则匹配，带优先级排序和匹配上限；无 LLM，无预算消耗。
- `distill.py` — 将已完成任务蒸馏为候选记录的 LLM（capability `"distill"`）；未知工具与 schema 校验门禁；`next_version` 次版本号递增；不受信任的任务记录在进入提示词前会先被清理。
- `evaluate.py` — `SkillEvaluator` 通过脚本化的 `_ReplayModel`（绝不使用真实 LLM）将评估用例回放到真实研究图中；确定性通过/失败基于匹配、注入 procedure 回显以及模型块完成；可检测对 baseline 版本的回归；可选 LLM judge 仅附加备注。
- `prompts.py` — distill 提示词构建器和 `format_approved_procedures` 可信提示词段。

### 图集成
- 新增 `match_skills` 节点（normalize → match_skills → retrieve_internal）：确定性匹配，发布 `skills_matched`，仅在状态中存储 `matched_skill_ids`（小对象规则）。
- `generate_grounded_answer` 通过 `PromptAssembler.build_answer_prompt(..., procedures_section=...)` 注入已审批 procedures 段。
- `build_research_graph(..., skill_registry=None)` —— 完全可选；无注册表的图表现与之前完全一致。
- 研究图现在复用 `harness/parse.py` 的 JSON/噪声辅助函数（从模型块阶段去重而来）。

### API (`/api/v1/skills`)
- `GET ""` —— 每个 family 最新 approved 版本（注册表视图）。
- `GET /pipeline?status=...` —— candidate/testing/deprecated 列表。
- `GET /{skill_id}` —— 版本族。
- `POST /distill {task_id}` —— 租户校验，仅已完成任务；结果**始终**是 candidate（模型输出无效/未知工具时返回 422）。
- `POST /{id}/{version}/evaluate {cases}` —— 确定性回放；candidate → testing 并记录通过率；保存评估运行记录。
- `POST /{id}/{version}/approve` —— 人工门禁；要求 testing 状态且 ≥1 个通过用例；记录 JWT 主体的 `approved_by`；同步刷新运行时注册表。
- `POST /{id}/{version}/deprecate` —— 退役版本；刷新注册表。

### 运行时接入
- Lifespan 构建 `PgSkillStore`（postgres URL）或 `InMemorySkillStore`，加载已审批 skills 到 `SkillRegistry`，传给图，并在关闭时关闭 store。
- `"distill"` 与 `"judge"` capabilities 已注册到 chat model。

### 清理
- 移除 `harness/registry.py` 中已废弃的占位 `SkillRegistry` dataclass（被真实注册表取代）。

## 验证

- `pytest`：160 passed, 1 skipped（torch block，本地 `importorskip`）—— 包含 23 个新测试：skills models/store/registry/distill/evaluate、图 skill 注入以及完整 API 生命周期（认证、approve 门禁、evaluate→approve→deprecate、distill 租户/完成校验）。
- `ruff check src tests`：clean。
- `mypy src`：clean（72 files）。

## 已知限制 / 后续工作

- 多副本注册表刷新（类似任务取消的 Redis pub/sub）尚未接入；第二个副本需要重启或手动刷新才能看到新批准的 skills。
- 前端尚无 Skills 面板；生命周期目前通过 API 驱动。
- LLM judge 已注册，但其备注按设计仅作为参考。
