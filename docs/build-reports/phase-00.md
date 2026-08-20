# Phase 00 构建报告

## 已交付
- 兼容性矩阵：`docs/compatibility-matrix.md`
- ADR 001：以 LangGraph 作为唯一的任务状态机
- ADR 002：PostgreSQL checkpointing
- ADR 003：基于 WeKnora 的 KnowledgeProvider 抽象
- 威胁模型草案：`docs/threat_model.md`

## 决策
- AgentOS/FastAPI 仅作为控制平面。
- LangGraph 是唯一的运行时状态机。
- WeKnora 通过 provider 接口访问，不直接导入。
- Phase 1 使用 FakeModel，不调用真实模型 provider。

## 验证
已通过 Phase 01 质量门：`uv sync --frozen`、`ruff check src tests`、`mypy src`、`pytest`、`docker compose up -d --build`、`scripts/smoke_test.sh`。
