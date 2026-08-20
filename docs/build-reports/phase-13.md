# 阶段 13 构建报告 — Skill 系统部署验证 + Distill 评估用例

Date: 2026-08-17
ADR: `docs/adr/014-skill-system.md` (updated)
Smoke: `scripts/smoke_test_skills.sh`

## 范围

弥合阶段 12 中"仅在内存模式下测试"的缺口：在真实 compose 栈（Postgres + Redis + 真实 qwen3:4b LLM + executor）上验证 skill 子系统，并让 distillation 生成建议评估用例，使审批循环从一个可用的评估套件开始。

## 交付内容

### 部署验证（真实栈、真实 LLM）
- `scripts/smoke_test_skills.sh` —— 针对 compose 栈的完整生命周期：
  1. preflight skills 端点；
  2. KV-cache 源任务（断言 *已完成* 的 `kv_cache_sim` 模型运行 —— 栈使用 FakeModel 时快速失败并给出提示）；
  3. 通过真实 qwen3:4b 进行 distill（最多 3 次尝试，422 被抛出并重试）；
  4. 确定性离线评估（构造用例，needle = procedure 前缀，因此门禁不依赖 4B 模型是否引用自身）；
  5. approve（记录 principal 的 `approved_by`）；
  6. 匹配触发条件的**新**任务必须发出携带 skill id 的 `skills_matched` SSE 事件（证明在新任务上 PgSkillStore → registry → 图注入生效）；
  7. 清理：deprecate，以便重跑时状态干净。
- 结果：**EXIT 0**。事后 Postgres 保存了完整生命周期记录（`skill_versions`：approved_by 为 `tenant-smoke:smoke-user`，cleanup 执行 deprecated；`skill_eval_runs`：1 条运行记录）。

### 改变代码的真实 LLM 发现
- qwen3:4b 对 skill_id 稳定输出 `kv_cache_simulation`（下划线），无论格式措辞如何 → `normalize_skill_id()` 改为强制转小写+短横线，而非拒绝（已实跑验证：拒绝 → 接受）。
- 模型混淆模型块名称与工具名称（`required_tools: ["kv_cache_sim"]`）→ 提示词现在明确 block names 不是 tools；未知工具门禁仍是硬兜底。
- 应用容器需要显式设置 `PK_AGENT_OLLAMA_CHAT_MODEL`（本机无 .env），否则 distill 会针对 FakeModel 立即失败。

### Distill 生成的评估用例
- Distill 提示词要求生成 2–4 个带逐字 procedure needle 的评估用例。
- `distill_candidate_record` 现在返回 `(record, eval_cases)`：用例被防御性解析（畸形条目丢弃），上限 5 个，且每个用例的 `expect_skill_matched` 被固定为实际 candidate id —— 模型不能建议用于"验证"另一个 skill 的用例。
- `POST /api/v1/skills/distill` 响应新增 `eval_cases`，可直接喂给 `POST .../evaluate`（后续也支持前端一键流程）。

## 验证

- `scripts/smoke_test_skills.sh`：EXIT 0（见上文）。
- `pytest`：完整套件通过（distill 单元测试覆盖 normalization、eval-case 解析/固定/丢弃；API 测试覆盖新响应结构）。
- `ruff` / `mypy`：clean。

## 备注

- compose 栈保持运行（db/redis/minio/app/executor），环境变量为 `PK_AGENT_OLLAMA_CHAT_MODEL=qwen3:4b`。
- 第一次冒烟尝试还发现 `docker compose up --build` 在 detached shell 中挂起；前台 `up -d` 正常 —— 这是运维备注，非代码问题。
