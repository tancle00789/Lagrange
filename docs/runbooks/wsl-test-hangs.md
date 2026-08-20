# WSL 测试挂起手册

## 症状

- `fastapi.testclient.TestClient` 在 `__enter__` 处挂起，或
- LangGraph 异步测试已通过断言，但 pytest 不再继续或退出。

## 诊断

首先在不导入 pk-agent 的情况下测试 Python 运行时：

```bash
python -c 'import asyncio; asyncio.run(asyncio.to_thread(lambda: 42)); print("done")'
```

如果 `42` 已计算但 `done` 始终未打印，说明该环境无法从工作线程唤醒 asyncio 事件循环。Starlette TestClient 使用 AnyIO blocking portal，LangGraph 的内存 checkpoint 路径可能使用默认 executor；两者暴露了相同的环境限制。

该问题在 Codex 托管的 WSL 沙箱中可复现。同一解释器、环境与测试套件在正常 WSL 进程命名空间中运行时可通过。这不是 pk-agent 死锁，不应通过弱化任务生命周期代码或添加任意 pytest 超时来解决。

## 解决

1. 安装锁定的开发依赖。Starlette 1.3+ 的 TestClient 使用 `httpx2`；它已显式包含在 dev 依赖组中。

   ```bash
   uv sync --frozen
   ```

2. 从正常 WSL/Windows 终端运行测试，而不是在会阻塞跨线程事件循环唤醒的受限容器或托管沙箱中运行。

   ```bash
   uv run pytest
   ```

3. 在 Codex 中，以沙箱提权方式运行 pytest 命令。测试本身不需要网络访问；它只需要正常的线程/事件循环语义。

## 2026-08-05 事故证据

- 受限沙箱：标准库 `asyncio.to_thread` 复现器返回值后，在事件循环关闭阶段挂起。
- 受限沙箱外：复现器打印了 `42` 与 `done`。
- 锁定 `httpx2` 后，全部 80 个 pk-agent 测试在受限沙箱外通过，且没有 TestClient 弃用警告。
