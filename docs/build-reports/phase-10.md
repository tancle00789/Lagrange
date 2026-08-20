# 阶段 10 构建报告 — 浏览器与部署验证

Date: 2026-08-05
Status: implementation complete; container runtime verified; host-port verifier pending WSL forwarding

## 交付范围

- 新增 Playwright 与 Chromium 任务流端到端测试，覆盖 token 录入、授权任务创建、请求载荷、刷新任务选择、 grounded 报告以及引用渲染。
- 新增前端生产构建/E2E 脚本以及 Make 目标。
- 新增应用容器健康检查，以及针对健康状态、持久化任务完成、跨租户拒绝、Redis Streams 状态、强制 PostgreSQL RLS 和 Prometheus 指标的确定性核心部署校验器。
- 新增 `.dockerignore`：实际构建中发现本地 virtualenv 和前端依赖进入了构建上下文。
- 固定容器内 `uv` 安装程序版本，并将运行时镜像改为非 root UID 并拥有应用/静态文件。
- 新增生产就绪检查清单与作品集 README。
- 修复生产启动路径：租户级 PostgreSQL checkpointer 继承 LangGraph 的 `BaseCheckpointSaver` 契约；应用现在可针对真实 PostgreSQL/Redis 核心栈启动。

## 已完成验证

| 门禁 | 结果 |
|---|---|
| 前端 `npm run build` | passed, Vite 8.2.0 |
| Chromium Playwright E2E | 1 passed |
| npm audit | 0 vulnerabilities |
| Compose YAML/config parse | passed |
| Deployment verifier shell syntax | passed |
| checkpointer 修复后的核心容器 | app healthy; task completed; cross-tenant read returned 404; Redis group pending=0; 3 forced RLS policies; Prometheus job metric emitted |

## 线上部署事件

Docker Desktop 28.5.2 起初连接成功并开始拉取/构建核心栈。在构建被故意中断以修复缺失的 `.dockerignore` 后，Docker Linux 引擎返回 API 500。重启 Desktop 后问题复现。主机日志显示 `docker-desktop` VM 卡在等待文件系统、`eth0` 和元数据就绪，随后出现 WSL vsock 超时。即便从当前 Ubuntu 会话执行 `wsl.exe --terminate docker-desktop` 也无法穿过失败的 vsock 边界。

重启 Docker Desktop/WSL 后，应用容器及其内部健康检查恢复。剩余的主机侧门禁是校验器访问 `localhost:8000`：Docker Desktop 当前通过其 Desktop 代理发布端口，而本 WSL 会话无法到达该代理，尽管应用在 Docker 网络内部是健康的。上述内部网络检查可作为运行时路径的证据，但主机侧脚本在端口转发路径恢复前不会标记为完全通过。

剩余命令：

```bash
docker compose up -d --build db redis app
DOCKER_CMD=docker.exe bash scripts/verify_core_deployment.sh
```

在该命令成功之前，不声称赞活容器/RLS 结果。
