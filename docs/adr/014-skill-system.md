# ADR 014:技能系统 —— 蒸馏候选、人工门控审批、确定性匹配

## 状态

已采纳(2026-08-17,Phase 7 / 构建报告 phase-12)

## 背景

docx §14 描述了技能生命周期:从已完成任务中蒸馏可复用流程,离线评估,
然后才允许影响后续运行。这补完了路线图的 Phase 7,并让运行时与
AgentOS 的技能概念对齐。

关键张力:

- LLM 擅长从已完成任务中提炼可复用流程 —— 但 LLM 绝不能单方面把行为
  发布进后续运行(否则经由被投毒任务记录的提示注入会变成持久的、受
  信任的 prompt 段落)。
- 技能*匹配*发生在每个任务上;在这里花模型预算或引入不确定性都不可
  接受。
- 评估必须证明注入 → 匹配 → 回答这条链路可用,同时不依赖真实 LLM
  (测试与 CI 没有 GPU 模型)。

## 决策

1. **生命周期即状态机,审批仅限人工。**
   `candidate → testing → approved → deprecated`(任何 approved 之前的
   状态也可直接进 `deprecated`)。生成代码(蒸馏、评估)只能在
   `candidate` 与 `testing` 之间创建或移动记录。
   `candidate/testing → approved` 迁移只存在于
   `POST /api/v1/skills/{id}/{version}/approve`,它从认证主体记录
   `approved_by = "{tenant}:{user}"`。批准某个版本会在同一事务中弃用
   同族所有更早的 approved 版本(发布 = 取代)。
2. **评估以确定性方式门控审批。** `SkillEvaluator` 用脚本化的
   `_ReplayModel`(绝非真实 LLM)把每条评估用例重放进*真实*研究图:
   候选技能按已批准加载,匹配必须在其触发条件上命中,注入的流程段落
   必须到达回答 prompt,期望的模型块必须完成。通过率是纯粹的断言计数。
   审批要求 `status == "testing"` *且*至少一条用例通过
   (`MIN_APPROVAL_PASS_RATE`)。可选的 LLM 评审(能力 `"judge"`)可以
   附注,但绝不改变通过/失败。蒸馏还会向模型索要 2–4 条建议评估用例;
   它们经防御性解析后钉在候选技能上(`expect_skill_matched`),让审批
   人从一套可用的评估套件起步,而不是空白页。
3. **匹配是确定性且零成本的。** `SkillRegistry` 只持有每个技能族的
   最新 approved 版本,从 store 显式加载(无隐式注册路径)。图的
   `match_skills` 节点匹配 keyword/regex `TriggerCondition` —— 无 LLM
   调用、无预算消耗 —— 且只在 state 中存 `matched_skill_ids`(小对象
   规则,ADR 002);记录在构建 prompt 时从内存注册表重读。匹配到的
   流程以*受信任*段落进入回答 prompt(`format_approved_procedures`)
   —— 受信任是因为通过了人工审批,并与不可信证据明确分隔。
4. **注册表刷新是显式的。** `approve`/`deprecate` 端点在 store 迁移后
   内联调用 `registry.refresh()`,然后经
   `RedisSkillRefreshCoordinator` 在 Redis 控制频道
   (`{prefix}:control:skills`)上发布,让每个副本都重新加载 —— 与
   任务取消同一套 pub/sub 模式(ADR 009)。单进程运行时使用 no-op 的
   `SkillRefreshCoordinator`。
5. **持久化镜像运行时存储。** `database_url` 为 postgres 时用
   Postgres(`skill_versions`、`skill_eval_runs`,幂等 DDL,
   `PgSkillStore`);否则用 `InMemorySkillStore`(开发/测试)。状态迁移
   使用 `SELECT ... FOR UPDATE`,并发 approve/deprecate 无法让生命周期
   分叉。

## 影响

- 被投毒的任务记录至多产出一个 `candidate`;在人工看过确定性的评估
  通过并点击批准之前,它无法到达任何未来任务的回答 prompt。
- 技能匹配给每个任务增加零次模型调用、微秒级正则开销。
- 评估器同时是技能链路的端到端回归测试:匹配、注入、模型块规划与
  回答解析。
- 蒸馏复用聊天模型,挂在能力 `"distill"` 下(与 `"answer"`/`"plan"`
  一样注册在 router 中);换更强的模型不需要动调用点。
- 本地 4B 模型系统性地违反输出格式细节(下划线技能 id、把块名当工具
  名)。因此蒸馏路径对 id 做归一化(`kv_cache_simulation` →
  `kv-cache-simulation`)并硬拒绝未知工具,而不是信任格式指令。已用
  qwen3:4b 在 `scripts/smoke_test_skills.sh` 中实测验证。
