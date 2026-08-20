# ADR 001:LangGraph 作为唯一的任务状态机

## 状态
Phase 1 已采纳。

## 背景
生产级知识 agent 需要暂停/恢复/取消、持久化执行、流式事件,以及控制面与任务运行时之间的严格边界。

## 决策
使用 LangGraph 作为唯一的核心任务状态机。AgentOS/FastAPI 负责 HTTP、认证、幂等、限流与事件传输。不允许在 LangGraph 节点内嵌套任何自主 agent 循环。

## 影响
- 运行时状态迁移是显式且可 checkpoint 的。
- 更高层的研究行为必须在后续阶段编译为图节点。
- WeKnora 保持为 `KnowledgeProvider` 之后的外部知识服务。
