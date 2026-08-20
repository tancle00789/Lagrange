# 阶段 11 构建报告 — 模型块系统（Phase 5′）

Date: 2026-08-10
Plan: `optimized-twirling-breeze.md` → "Phase 05′ 模型块系统实施计划"
ADR: `docs/adr/013-model-block-system.md`

## 范围

用户的第二大核心功能：agent 通过 LLM 自主决定是否运行预注册的模型块 —— 包括经典机器学习、深度学习或数值模拟 —— 以支持研究，结果与产物会呈现在回答和 UI 中。

## 交付内容

### modelblocks 模块 (`src/pk_agent/modelblocks/`)
- `models.py` — `ModelBlockSpec` / `ModelRunRequest` / `ModelRunResult`。
- `base.py` — `ModelBlock` 协议，`RunContext`（租户/任务/运行级产物 key）。
- `registry.py` — 显式注册目录，每个块带 Pydantic 参数校验（`extra="forbid"`）；为 API 提供 schema 丰富的目录，为计划提示词提供紧凑 JSON 摘要。
- `artifacts.py` — `ArtifactStore` 协议；`MinioArtifactStore`（通过 `asyncio.to_thread` 使用 minio SDK，延迟导入）和 `LocalArtifactStore`（防止路径遍历）。
- `sklearn_tabular.py` — logistic/RF 分类与回归，内联 JSON 行（≤2000×51）或合成 demo；包含 holdout 指标与 joblib 产物。
- `torch_mlp.py` — 可配置 MLP，CUDA 可用时自动启用并限制 50% 显存，生成 loss-curve JSON 与权重产物。
- `kv_cache_sim.py` — 无依赖的 KV-cache 内存/质量权衡模拟，覆盖多种压缩比。
- `broker.py` — `ModelRunBroker`（XADD 提交；pubsub + 1s 轮询等待，带 deadline）和执行器端 `run_model_worker`（消费者组，每块运行时上限通过 `wait_for` 控制，结果哈希 + 发布）。空读时休眠 `block_ms`，避免非阻塞 fake 忙等。
- `tools.py` — `model_block_run` ToolSpec（L2，`max_retries=0`），服务端 schema 重校验；提交/超时失败映射为 `TransientToolError`。

### 图
- 新增节点 `plan_model_calls`（capability `"plan"`，严格 JSON 计划，citation-gate 风格校验，≤`model_block_max_calls`）和 `run_model_blocks`（顺序 L2 工具调用，失败降级），连接在检索与答案生成之间。
- `generate_grounded_answer` 将已完成的运行作为可信的 "Simulation results" 提示词段接收；失败的运行不进入提示词。
- 状态新增 `model_calls` / `model_runs` / `model_error`（仅小对象）。预算计费：plan +1，每次执行运行 +1。

### 部署
- `executor_main.py` + `pk-agent-executor` 入口；Dockerfile 增加 `executor` 目标（uv `executor` 组：numpy/scikit-learn/torch；PyPI torch wheel 已打包 CUDA runtime；`TORCH_EXTRA_INDEX` ARG 兜底）。API 镜像不安装重型 ML 依赖。
- Compose：`executor` 服务（通过 deploy.devices 预留 GPU，`cpus: 4`，`mem_limit: 8g`）和 `minio` 服务（通过 daocloud 镜像），带健康检查启动；应用/执行器环境变量接入 MinIO 与超时配置。
- 配置：`model_blocks_*`、`model_run_result_ttl_seconds`、`minio_*`、`artifacts_local_dir`；`.env.example` 已更新。

### API + 前端
- `GET /api/v1/modelblocks`（带参数 schema 的目录）和 `GET /api/v1/artifacts/{id}`（租户前缀授权、防止遍历、按后缀推断 content-type）。
- `TaskStatusResponse.model_runs`；执行器结果持久化到任务记录。
- 前端：模型运行面板（块、状态、指标、通过认证 blob 下载产物），新增 SSE 事件类型，`listModelBlocks` / `downloadArtifact` API 辅助函数。

## 验证（本机）

| 门禁 | 结果 |
|---|---|
| `uv sync --frozen` | OK（lock 已更新：minio、sklearn、scipy、torch + CUDA wheels） |
| `ruff check src tests` | OK |
| `mypy src` | OK（63 files） |
| `pytest` | **137 passed, 1 skipped**（torch block 测试 —— 本地 dev 环境未安装 torch） |
| `npm run build` | OK（0 vulnerabilities 已预先验证） |
| `docker compose up -d --build` | OK —— app + executor 镜像构建完成（executor 阶段约 513s，torch 2.13.0+cu13 来自 PyPI）；完整栈健康 |
| `scripts/smoke_test.sh` | OK —— 真实 WeKnora + qwen3:4b，grounded report |
| `scripts/smoke_test_modelblocks.sh` | OK —— planner → `kv_cache_sim` → executor → MinIO 产物 → API 下载 |

### 部署验证（2026-08-11，Docker Desktop WSL2）
- `docker logs <executor>`：三个块已注册，MinIO bucket `pk-agent-artifacts` 已创建。
- GPU 透传已确认：`torch.cuda.is_available()=True` 在 **NVIDIA GeForce RTX 5070 Ti Laptop GPU** 上；直接通过 Redis stream 运行 `torch_mlp` 完成，`device_cuda: 1.0`，两个产物（loss curve + weights）均存入 MinIO。
- 端到端冒烟：preflight catalog 列出全部三个块；qwen3:4b 计划的任务执行了 `kv_cache_sim`，产出指标（`baseline_memory_gib`、`min_memory_gib`、`min_quality_proxy`、…）并通过 `GET /api/v1/artifacts/{id}` 下载曲线产物。
- MinIO 主机端口为 **19000/19001**（Windows 占用 9000）；容器内流量仍使用 `minio:9000`。

新增测试：块真实运行（sklearn demo/内联回归、kv_sim 曲线数学、参数拒绝）、产物存储（roundtrip、遍历）、注册表（重复/未知/schema）、工具处理（服务端重校验、transient 映射）、broker（fakeredis roundtrip、无效块、超时）、图（happy path、未知块、错误参数、max-calls 上限、不可解析计划、运行失败降级、无 capability/无注册表跳过、防御性解析）、API（目录、认证、产物 roundtrip/跨租户/遍历、`model_runs` 字段）。

### 值得注意的调试
- fakeredis 忽略 stream block 超时并立即返回：执行器循环忙等并饿死事件循环，导致 broker 测试挂起。修复方式：空读时休眠 `block_ms`（在真实 Redis 上同样正确）。
- redis-py 5.x 废弃 `PubSub.close()`，但 types-redis stubs 缺少 `aclose()` —— 运行时正确调用并加针对性 `type: ignore`。
- qwen3 是思考模型：其 `<think>…</think>` 痕迹泄漏到计划 JSON 中。修复方式：向 Ollama 发送 `"think": false`，并在图中增加防御性剥离（`_strip_model_noise` / `_load_json_object`）。
- 实跑发现 qwen3:4b 会提出 `compression_ratios=[0.1, …]`（语义相反）。范围校验从 `run()` 移到 `KvCacheSimParams` 字段校验器，使计划门禁拒绝它；块描述现在明确写出 "ratio r means memory is divided by r"。
- 本网络 TLS 阻断 docker.io —— 基础镜像通过 `docker.m.daocloud.io` 镜像拉取并重命名标签。
- 主机端口 9000 处于 Windows 保留范围（Hyper-V exclusion）—— MinIO 主机端口改为 19000/19001。

## 部署签核 — 2026-08-11 完成
1. Docker Desktop（WSL2 + GPU）已启动。✅
2. `docker compose up -d --build` —— executor 镜像使用 PyPI 的 CUDA torch 构建完成（无需自定义 index）。✅
3. Executor 日志显示三个已注册块以及 MinIO bucket 创建；通过实跑 `torch_mlp` 验证 GPU（`device_cuda: 1.0`）。✅
4. 在 `PK_AGENT_OLLAMA_CHAT_MODEL=qwen3:4b` 下：
   `bash scripts/smoke_test_modelblocks.sh http://localhost:8000` → EXIT 0
   （计划运行完成、指标存在、产物可下载）。✅

## 已知限制
- 内存运行时存储模式（测试）没有 Redis，因此 `model_block_run` 未注册；图会计划但在执行时降级。生产模式（`runtime_store=redis`）接入 broker。
- 执行器在运行中崩溃会使 stream 条目在消费者组中挂起（尚无 lease/autoclaim）；API 侧等待超时，运行被报告为失败。如需可参照 ADR 010 增加 lease 接管。
- 产物端点将整个文件流式读入内存 —— 对当前块输出（≤ 数 MB）足够；若块开始产生大文件则需重新考虑。
