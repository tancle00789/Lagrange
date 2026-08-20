# ADR 013:模型块系统 —— LLM 规划、Schema 门控、Executor 执行

## 状态

已采纳(2026-08-10,Phase 5′ / 构建报告 phase-11)

## 背景

agent 的第二大核心职能(按用户的产品定义):给定自然语言指令,运行
预注册的模型块(经典机器学习、深度学习、数值模拟)来支撑研究。源
docx 没有模型块概念;适用的约束来自工具系统(§13)、大产物的对象
存储(§8)、小对象图 State(§7)与沙箱要求(§18)。

关键张力:

- 用户希望由 LLM 决定*是否以及运行哪个*块 —— 但 LLM 绝不能注入任意
  代码或无界负载。
- 块依赖 sklearn/torch(重依赖、GPU),不能让 API 镜像膨胀或变慢。
- 训练出的模型与曲线是大体积二进制产物,绝不能进入图 State 或
  checkpoint 数据库。

## 决策

1. **LLM 自主规划,置于 schema 门之后。** `plan_model_calls` 图节点
   (能力 `"plan"`)向 LLM 索要严格 JSON
   `{model_calls: [{block_name, params}]}`。节点像引用门一样校验每次
   调用:块必须在 `ModelBlockRegistry` 中注册,params 必须通过该块的
   Pydantic schema(`extra="forbid"`),且至多保留
   `model_block_max_calls`(默认 2)次调用。非法调用被丢弃并记入
   `model_error`。规划 prompt 携带目录(名称、描述、参数类型),但不
   含任何用户控制的代码。
2. **经 ToolRegistry L2 工具在专用 executor 容器中执行。** 校验过的
   调用走工具 `model_block_run`(风险 L2,不自动重试)。API 侧 handler
   再次校验块 schema(图的规划不被信任),把 `ModelRunRequest` XADD
   到 Redis stream `{prefix}:modelruns`,并在
   `model_block_timeout_seconds` 内等待结果(pubsub + 1 秒轮询兜底)。
   executor 服务(同一代码库,`pk-agent-executor` 入口,stream 上的
   消费者组)运行块、上传产物并发布 `ModelRunResult`。
   - **为什么用常驻 executor 而不是 §18 式按任务沙箱:** §18 针对的是
     *任意不可信代码*。块是预注册的、受信任的、经过评审的代码,参数
     严格校验 —— 不存在代码注入面。带容器级资源限制(`cpus`、
     `mem_limit`、GPU 预留)的常驻 executor 提供同样的爆炸半径控制,
     且没有每次运行启动容器的开销。这是有意的、记录在案的放松;任何
     未来的*用户提交代码*功能必须使用完整的 §18 沙箱。
3. **产物进 MinIO,State 只存 id。** `ArtifactStore` 协议有两个实现:
   `MinioArtifactStore`(minio SDK 包在 `asyncio.to_thread` 中)与
   `LocalArtifactStore`(开发/测试)。产物 key 为
   `{tenant_id}/{task_id}/{run_id}-{filename}` —— 租户前缀即
   `GET /api/v1/artifacts/{id}` 强制的授权边界。图 State 只存
   `run_id`、块名、小体量指标 dict 与产物 id。回答 prompt 以受信任的
   "Simulation results" 段接收已完成运行的指标;失败的运行不进 prompt
   但保留在 state 中供 UI 展示。
4. **降级优于失败。** 当没有注册 planner 模型、registry 缺席或 LLM
   输出不可解析时,跳过规划(空调用)。运行失败记入 `model_error`,
   任务继续走向回答生成(与 ADR 006 外部源降级同一精神)。
5. **重依赖隔离在 executor 镜像。** sklearn/torch 位于 uv 的 `executor`
   依赖组,只在 Dockerfile 的 `executor` target(以及跑测试的 dev)
   中安装。API 镜像保持精简。PyPI 的 torch wheel 自带 CUDA 运行时,
   GPU 透传无需自定义 index;`TORCH_EXTRA_INDEX` 保留为构建 ARG 兜底。
   torch 块把 CUDA 显存限制在 50%
   (`set_per_process_memory_fraction`),单次运行不会饿死宿主 GPU。

## 首批三个块

- `sklearn_tabular` —— 对内联 JSON 行(≤2000×51)或合成演示数据做
  logistic/随机森林分类与回归;产物为 joblib 模型。
- `torch_mlp` —— 可配置 MLP(层数/epoch/lr/batch),有 CUDA 则用
  CUDA;产物为 loss 曲线 JSON + 权重。
- `kv_cache_sim` —— 零依赖的 KV-cache 显存/质量权衡曲线,跨压缩比
  扫描(支撑用户的 KV-cache 研究)。

## 影响

- LLM 可以触发 GPU/CPU 重负载:由 schema 校验、每任务至多 2 次调用、
  每块运行时上限、工具级超时与容器资源限制共同约束。预算记账把规划
  (+1)与每次运行(+1)计入 `model_budget_max_calls`。
- 超时的运行绝不自动重试(`max_retries=0`),因为 executor 副作用
  (产物)不是幂等的;Redis stream 保留该请求供运维检查。
- fakeredis 忽略 stream 阻塞读的超时:worker 在空读时 sleep
  `block_ms`,测试(以及任何非阻塞客户端)不会忙等空转。
- Compose 增加 `executor`(GPU 预留,4 CPU / 8 GiB 上限)与 `minio`
  服务;部署验证要求带 WSL2 GPU 支持的 Docker Desktop。
