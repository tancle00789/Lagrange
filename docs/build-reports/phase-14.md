# 阶段 14 构建报告 — 前端 Skills 面板 + 建议用例持久化

Date: 2026-08-17
ADR: `docs/adr/014-skill-system.md`
Depends on: phase-12 (skill system), phase-13 (deployment verification)

## 范围

让 skill 生命周期可日常可用：用真实 UI 替代 curl，并将 distill 建议的评估用例持久化，使"一键评估"在 distill 响应消失很久后仍能工作。

## 交付内容

### 后端
- `SkillRecord.suggested_eval_cases`（≤5，持久化在 JSONB payload 中 —— 向后兼容，旧记录默认为空）。Distillation 直接将用例嵌入候选记录。
- `POST .../evaluate` 的 body `cases` 现为可选：为空时回退到记录的建议用例（两者皆无则 422）。显式用例仍优先 —— 冒烟脚本中的构造用例不受影响。

### 前端 (`frontend/src`)
- **Skills 标签页**（应用级 Tasks/Skills 标签）：`SkillsPanel` 分三栏 —— Approved / Testing（待审批）/ Candidates。
  - Candidate → "Evaluate (N cases)" 运行建议用例并逐条显示通过/失败及失败原因。
  - Testing → 通过率徽章；仅在评估通过后才启用 "Approve"（与后端门禁一致）；可重新评估。
  - 任何非 deprecated 记录 → "Deprecate"。
  - 状态徽章复用任务状态色板（approved=green、testing=amber、candidate=blue、deprecated=red）。
- **任务详情**：已完成任务显示 "Distill skill" 按钮；结果提示指向 Skills 标签页（或报告 "not generalizable"）。
- SSE timeline 订阅新的 `skills_matched` 事件类型。
- api.ts/types.ts：完整 skill API 接口与类型。

### 卫生
- `npm audit fix`：nanoid 高危公告 → 0 vulnerabilities。
- `tsc -b && vite build` clean。

## 验证

- `pytest`：161 passed, 1 skipped（torch local）—— 完整套件通过。
- `ruff` / `mypy`：clean。
- 针对重建后的 compose 栈运行 `scripts/smoke_test_skills.sh`：**EXIT 0** —— distill 首次尝试即成功（关于 block names 与 tool names 的提示词澄清生效），Postgres 中 deprecated 记录带有 3 条持久化的建议评估用例。
- UI 从应用容器 `/` 提供（静态构建已烘焙进镜像）。

## 已知限制

- 面板仅使用建议用例进行评估；用例编辑器 UI 是后续工作（API 已接受显式用例）。
- 多副本注册表刷新仍未接入（approve/deprecate 的单副本刷新是内联执行的）。
