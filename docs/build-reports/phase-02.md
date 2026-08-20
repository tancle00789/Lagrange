# Phase 02 构建报告

## 交付内容
- WeKnora 作为知识子系统纳入 `docker-compose.yml`:
  `weknora-app` + `weknora-docreader` + `weknora-postgres`(ParadeDB)+
  `weknora-redis`,位于隔离的 `weknora-net`;`app` 是唯一的桥。
  WeKnora 仓库以只读方式被构建引用,绝不修改。
- Provider 契约层:`providers/contract.py`(KnowledgeHit /
  SourceReference / KnowledgeChunk / KnowledgeIngestionJob / CitationLocation)、
  `providers/errors.py`(Unavailable / Timeout / Auth / BadRequest / NotFound)。
- 薄传输层 `providers/weknora_client.py`:`X-API-Key` 认证、tenacity 有界
  重试(3 次,退避 <=8s,仅 5xx/连接错误)、类型化错误映射、不记录 secret。
- 真实的 `WeKnoraKnowledgeProvider`:原始 JSON -> 契约模型,片段截断
  (500 字符)后才允许进入图 state,租户 -> 知识库映射无跨租户回退。
- 测试用 `FakeKnowledgeProvider`;同一 Protocol,由同一套契约测试覆盖。
- LangGraph:`START -> init -> retrieve_knowledge -> echo -> finalize -> END`。
  检索故障降级为空命中 + `knowledge_error`(ADR 004);认证错误响亮失败。
  State 只携带 chunk_id/score/source/snippet。
- 引导脚本 `scripts/init_weknora.py`:健康等待、注册/登录、限定作用域
  API key、embedding 模型、知识库 —— 幂等,secret 只输出到 stdout。
- 真实集成 smoke `scripts/smoke_test_weknora.sh`(显式开启,不进 CI)。
- 文档:ADR 004、`docs/runbooks/weknora-ops.md`、威胁模型 Phase 2 节。

## 验证
```bash
uv sync --frozen          # ok(tenacity 加入 lock)
ruff check src tests      # ok
mypy src                  # ok,28 个文件
pytest                    # 20 passed
docker compose up -d      # 6 个服务启动,weknora-app healthy
bash scripts/smoke_test.sh http://localhost:8000          # smoke ok
python scripts/init_weknora.py                            # 租户/key/模型/知识库已创建
bash scripts/smoke_test_weknora.sh http://localhost:8000  # 真实 smoke ok
```

真实 smoke 证据:通过 WeKnora API 上传了 `smoke-quixotic-zephyr-*` 文档
(knowledge id `1b3cd041-...`),用宿主机 Ollama `qwen3-embedding:0.6b`
完成解析,pk_agent 任务 `75dca332...` 完成,标记串从真实 WeKnora chunk
流经图进入模型上下文。

## 构建过程中的显著偏差/修复
- `weknora-docreader` 复用本地已发布镜像(每次现编 ~4GB 源码不现实);
  保留 `build:` 以保证可复现。
- weknora-app 入口会 chown `/app/skills/preloaded`,因此 skills bind mount
  必须可写(与上游 compose 一致)。
- WeKnora 的 SSRF 校验默认拒绝 `host.docker.internal`;compose 为开发用
  Ollama 路径设置 `SSRF_WHITELIST_EXTRA=host.docker.internal`。
- Windows git-bash 的 curl 会弄坏带 `;filename=` 修饰符的 `/tmp`
  multipart 路径;smoke 脚本改用相对 cwd 的临时文件。

## 已知缺口(在威胁模型中跟踪)
- 每个部署一个默认知识库/key;按租户发放 WeKnora key 是未来工作。
- checkpoint 数据库尚无 RLS;WeKnora 卷无备份自动化。
