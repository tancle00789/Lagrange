# ADR 003:WeKnora 之上的 KnowledgeProvider 抽象

## 状态
Phase 1 已采纳。

## 背景
最终架构把 WeKnora 视为独立的知识服务。Phase 1 不得把 echo 运行时耦合到 WeKnora 内部实现。

## 决策
定义包含 `search`、`get_chunks`、`ingest` 与 `get_ingestion_status` 的 `KnowledgeProvider` Protocol。`WeKnoraKnowledgeProvider` 仅为客户端骨架;真实检索不在 Phase 1 范围内。

## 影响
- 后续阶段可以基于 WeKnora 实现混合检索而无需改动图契约。
- 测试可以使用 fake provider,且不得调用真实的模型或知识服务。
