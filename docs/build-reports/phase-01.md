# Phase 01 构建报告

## 已交付
- Echo LangGraph：`START -> init -> echo -> finalize -> END`
- 带身份验证、限流、幂等、SSE 事件、恢复、取消的 AgentOS/FastAPI 任务 API
- 带内存回退的 PostgreSQL checkpointer factory，用于测试
- Harness 骨架：ModelRouter、ToolRegistry、SkillRegistry、PromptAssembler、AgentEventBus
- KnowledgeProvider 协议与 WeKnora 客户端骨架
- 仅使用 FakeModel 的单元/集成测试

## 验证
本地与 Docker Compose 中通过：

```bash
uv sync --frozen
ruff check src tests
mypy src
pytest  # 5 passed
docker compose up -d --build
bash scripts/smoke_test.sh http://localhost:8000  # smoke ok
```

Smoke test 通过 FastAPI 控制平面创建了一个任务，并验证它通过 FakeModel echo 路径到达 `completed`。
