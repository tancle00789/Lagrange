# Phase 15 构建报告 —— 多副本技能注册表刷新 + 评估用例编辑器

日期:2026-08-18
ADR:`docs/adr/014-skill-system.md`(决策 4 已更新)
依赖:phase-12(技能系统)、phase-13(部署验证)、phase-14(前端面板)

## 范围

补完 Phase 7 最后两项遗留:跨副本技能注册表失效广播,以及技能面板中
的评估用例编辑器 —— 让建议用例在评估前可以被审阅/编辑,而不是原样
照单全收。

## 交付内容

### 后端
- `pk_agent/skills/refresh.py`:
  - `SkillRefreshCoordinator` —— 单进程运行时(开发/测试)的 no-op
    基类,形状与取消协调器一致。
  - `RedisSkillRefreshCoordinator` —— 基于 `{prefix}:control:skills`
    的 pub/sub;`start(refresh)` 派生一个带 ready-Event 握手的监听任务
    (模式与 `RedisCancellationCoordinator` 一致,ADR 009),
    `request()` 发布消息,`aclose()` 干净地取消监听器。
- `agentos/main.py` lifespan:运行时存储为 Redis 时构建 Redis 协调器
  (否则为 no-op),以 `refresh_skills_safe` 包装启动(异常记录日志,
  监听器永不退出),关闭时清理。
- `api/v1/skills.py`:`approve`/`deprecate` 现在走
  `_publish_registry_change()` —— 先本地 `registry.refresh()`(本副本
  立即正确),再 `skill_refresh.request()`(每个副本重新加载;本地
  监听器再触发一次是无害的幂等重复刷新)。

### 前端
- `api.ts`:`evaluateSkill(skillId, version, cases?)` —— 显式传入的
  用例覆盖后端的建议用例兜底。
- `SkillsPanel`:候选/测试中记录的按技能折叠的"评估用例 (N)"编辑器
  —— 首次展开时以 `suggested_eval_cases` 播种;可添加(≤20)/删除/
  编辑查询与 needles(每行一条);"评估"提交编辑后的草稿,全空草稿在
  客户端拒绝。
- 样式:`.case-editor` / `.case-row`,`.actions` 垂直居中。

### 测试
- `test_coordination.py::test_redis_skill_refresh_reaches_other_replicas`
  —— 两个 fakeredis 支撑的协调器,一次发布唤醒两个监听器。
- `test_skills_api.py::test_approve_and_deprecate_notify_other_replicas`
  —— app state 上的记录器协调器断言每次 approve/deprecate 恰好调用
  一次 `request()`。

## 验证

- `pytest`:163 passed,1 skipped(本地 torch)—— 全套件绿。
- `ruff` / `mypy`(73 个文件):干净。
- `tsc -b && vite build`:干净。
- 真实栈复验(`PK_AGENT_OLLAMA_CHAT_MODEL=qwen3:4b`,完整 compose 含
  executor):`scripts/smoke_test_skills.sh` **EXIT 0** —— 蒸馏(真实
  qwen3:4b)→ 评估 → 批准 → 新任务的 SSE 携带 `skills_matched` 与已
  批准技能 id。(前两次尝试是环境性失败:Docker Desktop 重启后 executor
  容器与宿主机 Ollama 未启动;不是代码问题。)

## 已知限制

- 跨副本刷新由单元测试(fakeredis)与单副本 smoke 验证;真正的双副本
  compose profile 尚未测试(当前是单副本部署)。
