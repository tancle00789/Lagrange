# 威胁模型 — Phase 09

## 资产
- 租户的任务输入与运行产出
- Checkpoint 状态
- 模型/工具预算
- WeKnora 限定作用域的 API key、base URL 与知识库映射
- 检索到的知识片段(租户机密内容)
- 外部文献元数据(不可信的第三方内容)
- GitHub token、Springer Nature API key(可选外部凭据)
- JWT / 开发用 bearer 令牌

## 信任边界
- HTTP 客户端 -> AgentOS/FastAPI
- AgentOS -> LangGraph 运行时
- LangGraph -> PostgreSQL checkpoint 存储
- LangGraph -> WeKnora HTTP API(Phase 2):通往知识数据的唯一路径
- LangGraph -> 外部文献 API(Phase 4'):arXiv、GitHub、CrossRef、
  Springer Nature —— 跨越此边界的一切内容均不可信
- 后续阶段:对象存储

## Phase 1 控制
- Bearer 认证;`dev:{tenant}:{user}:{roles}` 仅用于 Phase 1 开发,JWT 路径已预留。
- 通过任务记录上的 `tenant_id` 与 `{tenant_id}:{task_id}` checkpoint thread id 实现租户隔离。
- 创建任务要求 admin 角色。
- 进程内按租户限流器。
- 任务创建支持 Idempotency-Key。
- FakeModel 仅用于测试;不做真实模型调用。
- 图 state 只存小对象元数据,不存文档、chunk、PDF 或网页。

## Phase 2 控制(WeKnora 子系统)
- 业务代码永不直连 WeKnora 数据库;HTTP API 是唯一接口,且只有 `app`
  容器可达(`weknora-net`)。
- WeKnora 访问使用限定作用域的 API key(`X-API-Key`);key 通过环境变量
  注入,绝不打印日志,客户端只记录 method/path/status code。
- 租户 -> 知识库映射为显式配置;未映射的租户直接报错,绝不回退到其他
  租户的知识库。
- 片段在 provider 侧截断(默认 1500 字符),checkpoint state 与 SSE 事件
  无法借此泄露完整文档。
- 检索重试有界(3 次,退避 <=8s,仅 5xx/连接错误)—— 不对已降级的
  子系统无限重试。
- WeKnora 的认证失败让任务响亮地失败;只有可用性/超时失败才降级为空
  上下文,且每次降级都会发出 `knowledge_degraded` 事件。
- 真实集成 smoke(`scripts/smoke_test_weknora.sh`)为显式开启,绝不允许
  mock 冒充集成证据。

## Phase 3 控制(研究工作流 + Web UI)
- 回答模型被要求输出严格 JSON,但输出永不被信任:图会剥除代码围栏,
  解析失败时回退纯文本,且 `citation_gate` 节点丢弃所有并非真实检索到
  的 chunk_id,LLM 无法伪造引用。
- Claim 文本截断(600 字符),片段保持有界(1500 字符)才进入图 state ——
  检索内容的提示注入无法把大载荷偷运进 checkpoint。
- 浏览器 SPA 由同一 FastAPI 应用提供(无额外信任边界);CORS 仅对 Vite
  开发源 `http://localhost:5173` 开放。
- EventSource 无法设置请求头,因此 SSE 认证也接受 `?token=` 回退。URL
  中的令牌有经代理/访问日志泄露的风险 —— 对开发令牌格式可接受,但必须
  与下文 JWT 工作一起替换为签名的短寿命 SSE ticket(或 cookie 认证)。
- `ollama_chat_model` 未设置 ⇒ FakeModel:测试与 CI 永不调用真实模型。

## Phase 4' 控制(文献子系统)
- 外部检索只经由形式化的 `ToolRegistry` 运行:显式注册、按调用风险门控
  (search=L1,PDF 灌库=L2)、有界超时、重试仅限瞬时错误。无隐式工具
  注册,无无限重试。
- SSRF:PDF 下载只允许 https URL 且域名在配置的允许列表内;重定向手动
  跟随(最多 3 次),每一跳都重新对照允许列表校验。arXiv/GitHub/
  CrossRef/Springer 检索端点由配置硬编码,不来自模型输出。
- 外部内容不可信:记录只携带元数据 + 摘要(≤2000 字符),标题/摘要以
  证据块形式进入 prompt,引用门对每一个 source_id 对照真实检索到的记录
  校验 —— LLM 同样无法伪造外部引用。
- 版权边界(ADR 006):仅当任务显式设置 `ingest_external=true` 时才抓取
  完整 PDF 并灌入 WeKnora;默认流程只取元数据。下载有大小上限(25MB)、
  校验 content-type,且每个任务最多 2 个文件。
- 外部凭据(GitHub token、Springer key)经环境变量注入,绝不打印日志;
  未配置时 Nature 源直接不注册。
- 外部源失败按源降级(记入 `external_error`,以 `external_degraded` 事件
  呈现),永不让任务失败 —— 可选增强不能成为核心工作流的拒绝向量。
- arXiv 的 3 秒限速在客户端强制;CrossRef polite-pool mailto 可配置。
- 真实集成 smoke(`scripts/smoke_test_literature.sh`)访问真实 arXiv API,
  网络不可达时报告 SKIP(退出码 2)—— skip 绝不被报成 pass。

## Phase 05 控制(生产认证边界)

- `PK_AGENT_JWT_SECRET` 为开发默认值或短于 32 字符时,生产启动失败即
  关闭。`PK_AGENT_ENVIRONMENT=production` 时拒绝开发 bearer 令牌。
- 签名 JWT 必须有过期、subject 与 tenant 声明;tenant、subject 与 roles
  声明在成为授权上下文之前先校验类型。
- 长寿命 bearer 令牌不再进入 EventSource URL。由已认证端点签发随机的、
  绑定任务/租户的 SSE ticket,默认寿命 60 秒;每张 ticket 在首次连接
  尝试时即被消费。
- 缺少角色时任务创建返回 HTTP 403。幂等记录按租户隔离,防止跨租户的
  key 冲突与任务 ID 泄露。

## Phase 06 控制(持久化任务控制面)

- 生产拒绝进程内运行时存储。任务记录与租户级幂等映射保存在专用 Redis
  服务中;原始幂等 key 经 SHA-256 哈希后才成为 Redis key。
- 任务更新是条件化状态迁移。完成/失败写入只从活跃状态才被接受,因此
  来自同副本或其他副本的迟到图结果无法覆盖取消。
- 本地持有的后台任务用 `asyncio.Task.cancel` 真正取消。运行时关闭时把
  被中断的活跃工作标记为失败,而不是留下陈旧的 `running` 记录。
- 任务记录与租户索引保留期有界;Compose 部署中 Redis 使用 AOF。WeKnora
  继续使用独立的 Redis 信任域。

## Phase 06B 控制(分布式运行时协调)

- 租户限流使用共享 Redis 固定窗口;租户标识符经 SHA-256 哈希后才进入
  限流 key。
- SSE ticket 共享、TTL 有界,并用 Redis `GETDEL` 原子消费,副本路由不
  削弱一次性语义。
- 任务事件与取消控制使用命名空间化的 Redis Pub/Sub 频道。取消先提交
  终态再发送控制消息,因此丢失的消息不可能让迟到的完成结果逆转该决定。
- 跨实例测试使用两个客户端与两个应用 lifespan 共享一个 FakeRedis 服务,
  包括在不持有图协程的副本上发起的端到端取消请求。

## Phase 07 控制(持久化 worker 执行)

- Redis Streams 消费者组保留未确认的工作。worker 只确认终态结果,并用
  心跳租约加 `XAUTOCLAIM` 从已停止的进程接管工作。
- 持久化的尝试次数为瞬时图失败划界。意外的消费者错误不会杀死 worker
  循环,也不会确认受影响的投递。
- 优雅关闭把活跃工作移入 `retrying`;用户取消仍是终态,条件化任务迁移
  拒绝迟到或重复的结果。
- 投递语义明确为至少一次。任何未来的外部写工具都必须先实现幂等 key,
  才能被视为生产安全。

## Phase 08 控制(身份、租户数据库隔离、提示注入)

- 生产 JWT 校验使用非对称 JWKS 密钥,固定算法白名单,issuer/audience
  精确匹配。未知 key ID 触发一次缓存刷新,在支持轮换的同时拒绝算法
  混淆攻击。
- 租户 ID 不允许包含 checkpoint 分隔符。worker 信封在执行图之前必须与
  持久化任务的租户和 thread 匹配。
- 所有 LangGraph checkpoint 表强制启用 PostgreSQL RLS。每个 saver 操作
  都设置事务级租户上下文;缺失或不匹配的上下文无法读写其他租户的
  checkpoint 行。
- 检索文本有界、以不可信 JSONL 序列化,并剥除常见的模型控制符语法。
  疑似注入的证据发出事件,但绝不改变静态图路由或工具风险许可。
- 回答模型无工具调用能力。严格输出解析与引用门把可接受的 claim 限制
  在真实检索证据的标识符范围内。

## Phase 09 控制(可观测性数据)

- 日志脱敏 bearer 值、URL 中的 token/key/secret 值与敏感结构化字段。
  原始租户 ID 替换为稳定的截断哈希。
- Prometheus 标签只含路由模板与状态码,绝不含任务 ID、原始路径、
  prompt 或租户 ID,同时约束了泄露面与基数。
- Trace 上下文跨越持久队列,但 span 只含标识符与计时;prompt、检索
  证据与凭据一律排除。
- 指标端点明确为内部部署面,必须经网络或入口策略限制访问。

## Phase 11 控制(模型块子系统,Phase 5′)

- LLM 规划器只能点名预注册块;每次规划的调用都经 schema 门控(注册块
  + Pydantic 参数,`extra="forbid"`,≤2 次调用),工具处理器在服务端
  再次校验。LLM 输出被视为校验器的不可信输入,永远不是可执行意图。
- `model_block_run` 是 L2 工具且 `max_retries=0`:超时的运行已有
  executor 侧副作用(产物),绝不静默重复执行。
- 块是受信任的预注册代码 —— 不存在用户提交代码的路径。容器级限制
  (`cpus`、`mem_limit`、GPU 预留、torch 块 50% CUDA 显存上限)约束
  爆炸半径。每块 `max_runtime_seconds` 由 executor 中的
  `asyncio.wait_for` 强制。
- 参数 schema 约束所有负载维度(行数/特征数/epoch 数/层数/上下文长度),
  LLM 无法请求无界算力。
- 产物存于 `{tenant_id}/{task_id}/...` 之下;下载端点强制租户前缀并
  拒绝路径穿越(`..`),本地存储相对其根目录解析路径。图 state 与
  checkpoint 只保存产物 id 与小体量指标 —— 绝不存二进制载荷。
- MinIO 凭据经环境变量注入为 secret;绝不打印日志,broker/executor
  只记录 run_id、块名、租户与耗时。
- prompt 中的"模拟结果"段只含系统生成的指标(无不可信文本),
  executor 输出无法成为注入回答模型的向量;失败的运行不进 prompt。
- Redis stream/hash 仅内部使用;executor 除产物存储外无需外部网络访问。

## 上生产前的已知缺口
- 把审批流扩展到工具级:L3 外部写工具目前依赖任务级显式开关,尚无
  按调用的 HITL 中断。
- 按租户发放 WeKnora API key(Phase 2 每个部署使用一个默认知识库/key)。
- 为 Redis Pub/Sub 监听器增加重连/退避与运维告警;正确性在丢失取消
  消息时仍成立,但计算可能不会立即停止,要等当前调用返回。
