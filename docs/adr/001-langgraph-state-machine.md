# ADR 001: LangGraph as the only task state machine

## Status
Accepted for Phase 1.

## Context
The production knowledge agent requires pause/resume/cancel, durable execution, streaming events, and a strict boundary between control plane and task runtime.

## Decision
Use LangGraph as the only core task state machine. AgentOS/FastAPI owns HTTP, auth, idempotency, rate limits, and event transport. No nested autonomous agent loop is allowed inside a LangGraph node.

## Consequences
- Runtime state transitions are explicit and checkpointable.
- Higher-level research behavior must be compiled into graph nodes in later phases.
- WeKnora remains an external knowledge service behind `KnowledgeProvider`.
