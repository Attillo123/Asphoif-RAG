# Asphoif RAG

企业级 RAG（Retrieval-Augmented Generation）模块化单体基线，提供知识库管理、文档入库、Dense + BM25 混合检索、RRF 融合、Redis 缓存、SSE 流式问答、链路 Trace 和离线评估能力。

项目面向企业内部知识库场景，重点解决权限隔离、文档版本、可恢复入库、检索降级、引用返回和效果评估问题。

## 功能概览

- 用户登录、JWT Access Token、`personal`/`admin` 角色
- 知识库创建、查询和软删除
- Markdown、Markdown 扩展名和 TXT 文档上传
- 内容 SHA-256 幂等判断，避免重复创建文档版本
- 固定窗口切块、稳定 Chunk ID、任务状态机和失败重试
- Qwen OpenAI-compatible Embedding，固定 1024 维向量
- OpenSearch 2.12.0：IK 分词、BM25、k-NN 向量检索和元数据过滤
- Dense/Sparse 并行召回与 RRF 融合
- Redis Embedding/检索结果缓存、短锁和缓存降级
- OpenAI-compatible Chat API，通过 SSE 返回增量答案和引用
- Query Trace、首 Token 延迟、完整脱敏答案和检索快照
- Recall、MRR、nDCG、RAGAS、引用评估和人工评审
- React 测试控制台，支持 Chat、文档入库、检索调试和评估回归

## 系统架构

```mermaid
flowchart LR
    U[浏览器 / API Client] --> F[React + Nginx]
    F --> A[FastAPI API]
    A --> R[Redis Cache]
    A --> M[(MySQL)]
    A --> O[OpenSearch]
    A --> L[OpenAI-compatible Chat]
    W[Ingestion Worker] --> M
    W --> O
    W --> E[Qwen Embedding API]
    A --> T[Query Trace / Evaluation]
```

API 保持无状态，MySQL 保存业务数据和 Trace，Redis 保存缓存与短期锁，OpenSearch 保存可检索 Chunk。入库 Worker 与 API 使用相同后端镜像，独立处理解析、切块、Embedding 和索引任务。

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 后端 | Python 3.10、FastAPI、SQLAlchemy 2.x、Alembic |
| 依赖管理 | uv |
| 数据库 | MySQL |
| 缓存 | Redis |
| 检索 | OpenSearch 2.12.0 + IK |
| 模型接口 | OpenAI-compatible Chat、Qwen Embedding |
| 前端 | React、TypeScript、Vite |
| 部署 | Docker、Docker Compose、Nginx |

## 项目结构

```text
.
├── backend/
│   ├── app/
│   │   ├── api/             # HTTP API、SSE
│   │   ├── core/            # 配置、日志、安全、错误处理
│   │   ├── db/              # SQLAlchemy、数据库会话、ID
│   │   ├── ingestion/       # 解析、切块、Embedding、索引
│   │   ├── models/          # ORM 模型
│   │   ├── retrieval/       # Dense、Sparse、RRF、熔断、Deadline
│   │   ├── services/        # 业务服务
│   │   └── evaluation/      # 指标、RAGAS 适配、评估服务
│   ├── alembic/             # 数据库迁移
│   ├── tests/
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/                # React 测试控制台
├── docker/                  # Worker、Nginx 配置
├── docs/                    # 架构、接口、缓存、评估文档
├── docker-compose.yml       # 外部依赖模式部署
├── .env.example             # 通用配置模板
└── .env.docker.example      # Docker 部署配置模板
```

## 快速开始

### 后端

要求 Python 3.10 和 uv。MySQL、Redis、OpenSearch 以及模型 API 必须可访问。

```bash
cd backend
uv sync --group dev
cd ..
cp .env.example .env
# 编辑 .env，填写外部服务和模型 API 配置

cd backend
uv run alembic upgrade head
uv run python -m app.cli init-opensearch
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

创建管理员：

```bash
uv run python -m app.cli create-user \
  --username admin \
  --password 'change-this-password' \
  --role admin
```

检查服务：

```bash
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
```

### 前端开发

```bash
cd frontend
npm ci
npm run dev
```

Vite 开发服务器会把 `/api` 和 `/health` 代理到 `http://127.0.0.1:8000`。也可以设置 `VITE_API_BASE_URL` 指向其他后端地址。

## Docker Compose 部署

当前 Compose 只运行前端、API、Worker 和迁移服务，不创建 MySQL、Redis 或 OpenSearch。三项基础设施必须在 `.env` 中配置为已存在的服务器地址。

```bash
cp .env.docker.example .env
```

至少修改以下配置：

```env
DATABASE_URL=mysql+pymysql://user:password@your-mysql:3306/rag
REDIS_URL=redis://:password@your-redis:6379/0
OPENSEARCH_URL=http://your-opensearch:9200
OPENAI_API_KEY=your-chat-api-key
QWEN_EMBEDDING_API_KEY=your-embedding-api-key
JWT_SECRET_KEY=a-random-secret-with-at-least-32-characters
```

构建并启动：

```bash
docker compose build
docker compose up -d
docker compose ps
```

`migrate` 服务会执行 `alembic upgrade head`，然后初始化 OpenSearch 物理索引和读写 Alias。首次部署后创建管理员：

```bash
docker compose run --rm api python -m app.cli create-user \
  --username admin \
  --password 'change-this-password' \
  --role admin
```

默认前端端口为 `80`，也可以在 `.env` 设置 `FRONTEND_PORT=8080`。常用运维命令：

```bash
docker compose logs -f api worker migrate
docker compose restart api worker
docker compose up -d --build
docker compose down
```

更多服务器部署说明见 [DEPLOYMENT.md](./DEPLOYMENT.md)。

## API 入口

除健康检查外，接口需要携带 `Authorization: Bearer <access_token>`。

| 能力 | 方法 | 路径 |
| --- | --- | --- |
| 存活检查 | GET | `/health/live` |
| 依赖就绪检查 | GET | `/health/ready` |
| 登录 | POST | `/api/v1/auth/login` |
| 当前用户 | GET | `/api/v1/auth/me` |
| 知识库 | GET/POST/DELETE | `/api/v1/knowledge-bases` |
| 文档列表与详情 | GET | `/api/v1/documents`、`/api/v1/documents/{id}` |
| 上传文档 | POST | `/api/v1/knowledge-bases/{id}/documents` |
| 入库任务 | GET/POST | `/api/v1/ingestion-jobs/...` |
| 混合检索 | POST | `/api/v1/retrieval/search` |
| SSE 问答 | POST | `/api/v1/chat/completions` |
| 评估快照与 Run | GET/POST | `/api/v1/evaluations/...` |

完整接口约定见 [docs/接口文档.md](./docs/接口文档.md)，SSE 事件 Schema 见 [docs/schemas/sse-event.schema.json](./docs/schemas/sse-event.schema.json)。

## Worker 与 CLI

Worker 会持续领取 `PENDING` 和 `RETRY_WAITING` 任务：

```bash
uv run python -m app.cli process-next-ingestion-job
```

常用命令：

```bash
uv run python -m app.cli process-ingestion-job --job-id <job-id>
uv run python -m app.cli retry-ingestion-job --job-id <job-id>
uv run python -m app.cli reindex-ingestion-job --job-id <job-id>
uv run python -m app.cli init-opensearch
```

## 配置与安全

- 不要提交 `.env`、API Key、数据库密码或 JWT 密钥。
- 生产环境 `APP_ENV=prod` 时，`JWT_SECRET_KEY` 至少 32 个字符。
- Embedding 维度首期固定为 `1024`，必须与 OpenSearch mapping 一致。
- `personal` 用户只能访问自己的知识库；`admin` 可管理全部资源，但检索仍需明确指定知识库。
- Trace 默认保存脱敏后的答案、Chunk ID、分数和链路信息，不保存完整 Prompt 和原始敏感输入。
- 生产环境建议通过密钥管理服务注入敏感配置，并限制 OpenSearch、Redis 和数据库网络暴露范围。

## 测试与质量检查

```bash
cd backend
uv run ruff check app tests
uv run pytest

cd ../frontend
npm run build
```

## 当前限制

- 当前正式解析器支持 Markdown 和 TXT；PDF、Word 仅保留统一接口，尚未实现具体解析器。
- Chat、Embedding 和可选 Rerank 依赖外部 OpenAI-compatible 服务。
- 当前项目是可扩展的模块化单体基线，生产规模化仍需根据实际负载补充反向代理、监控、备份、限流和高可用策略。

## 文档索引

- [架构设计](./docs/RAG架构.md)
- [开发阶段与验收记录](./docs/开发阶段文档.md)
- [缓存设计](./docs/RAG缓存设计.md)
- [优化链路](./docs/RAG优化链路.md)
- [评估方案](./docs/RAG评估.md)
- [部署说明](./DEPLOYMENT.md)
