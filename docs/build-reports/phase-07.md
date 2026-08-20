# Phase 07 构建报告 —— 持久化 worker 租约与恢复

日期:2026-08-05

## 交付范围

- 为 Redis 运行时模式新增 Redis Streams 消费者组队列。
- 新增紧凑任务信封、可配置 worker 并发、有界持久化尝试次数、重试状态、
  心跳租约续期、`XAUTOCLAIM` 接管与终态 `XACK` 语义。
- 优雅关闭从终态失败改为可恢复的 `retrying`;硬进程丢失在租约到期后
  从 pending entry 列表恢复。
- 用户取消跨副本保持终态,恢复的投递无法覆盖任务终态。
- 新增 worker 错误遏制:意外的投递失败让 entry 保持可恢复,而不会永久
  杀死消费者循环。
- 新增 ADR 010 与 Redis worker 运维手册。

## 验证

| 关卡 | 结果 |
|---|---|
| `ruff check --no-cache src tests` | ok |
| `mypy src` | ok,49 个源文件 |
| 队列/执行/协调聚焦测试 | 12 passed |
| 受限 WSL 沙箱外的完整 pytest | 92 passed |

聚焦覆盖证明了 stream 往返与 ACK、瞬态重试、最大尝试次数失败、空闲
entry 崩溃恢复、关闭接管、意外错误后消费者存活,以及共享 worker 下的
跨副本取消。

## 显式边界

运行时提供至少一次投递,而非精确一次副作用。外部写工具必须自带幂等
契约。真实 Redis 与容器部署仍属于最终环境验证阶段。
