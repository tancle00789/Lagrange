# ADR 005:Ollama 作为 ModelRouter 之后的首个真实模型 provider

## 状态

已采纳(Phase 3)

## 背景

Phase 1–2 只运行 `FakeModel`。Phase 3 引入有据可依的回答生成,需要真实
LLM 才能做有意义的端到端运行,同时测试绝不能调用真实模型(硬约束)。
运行时还必须完全离线可用:默认路径不需要外部 API key,不依赖公网。

## 决策

- `ModelRouter` 按**能力**路由(如 `"answer"`),不按厂商。
- `Model` 协议为 `async def generate(prompt, *, capability) -> str`。
- `OllamaModel`(`harness/ollama.py`)是首个真实 provider:调用宿主机
  上的 Ollama(`POST {base}/api/chat`,`stream: false`),超时时间可
  配置,仅对传输/超时错误重试一次。
- 接线位于 `agentos/main.py` 的 lifespan:若设置了
  `PK_AGENT_OLLAMA_CHAT_MODEL`,能力 `"answer"` 获得 `OllamaModel`;
  否则为 `FakeModel`。测试与 CI 不设置它,因此永不触碰真实模型。
- 模型被要求输出严格 JSON(`PromptAssembler`),但图永不信任它:
  `_parse_model_output` 防御性地剥除代码围栏,解析失败回退纯文本,
  `citation_gate` 节点丢弃任何并非真实检索到的 chunk_id。

## 影响

- 真实模型运行需要在宿主机上一次性执行 `ollama pull qwen3:4b`;
  compose 经 `host.docker.internal`(`extra_hosts: host-gateway`)访问
  宿主机。
- 离线/气隙部署从构造上得到支持(本地模型)。
- 未来切换云端 provider 只是新增一个 `Model` 实现加 `main.py` 中的一处
  接线改动;图代码不变。
- LLM 的不确定性被约束在引用门之后:一篇流畅但无依据的回答会降级为
  合成 claim 或 `grounded=False`,绝不会出现伪造引用。
