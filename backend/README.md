# Asphoif RAG Backend

阶段 0 提供 FastAPI 基础工程和依赖就绪检查。后端目标运行时为 Python 3.10，依赖使用 `uv` 管理。

## 启动

在 `backend` 目录执行：

```powershell
uv sync --group dev
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

如果使用当前解释器直接验证，可在 `backend` 目录执行：

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

默认从仓库根目录的 `.env` 读取配置。不要提交 `.env`；变量模板在仓库根目录的 `.env.example`。

## 阶段 0 验证

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

`/health/ready` 会并行检查 MySQL、Redis 和 OpenSearch，并通过 `/_cat/plugins?format=json` 验证 IK 插件。任意必要依赖不可用或 IK 未安装时返回 HTTP 503。

## 数据库迁移

阶段 1 的初始迁移已经生成。先检查 SQL，再连接目标数据库执行：

```powershell
uv run alembic upgrade head
```

查看当前版本和迁移历史：

```powershell
uv run alembic current
uv run alembic history
```

迁移读取根目录 `.env` 中的 `DATABASE_URL`。应用内部会将 `mysql+pymysql://` 自动适配为异步 SQLAlchemy 使用的 `mysql+aiomysql://`；Alembic 也使用同一环境变量。

## 阶段 2 认证和权限

先执行阶段 2 的增量迁移：

```powershell
uv run alembic upgrade head
```

使用管理 CLI 创建首个用户。密码只用于生成 Argon2id 哈希，不会写入数据库：

```powershell
uv run python -m app.cli create-user --username admin --password "change-me" --role admin
```

接口：

```text
POST /api/v1/auth/login
GET  /api/v1/auth/me
POST /api/v1/knowledge-bases
GET  /api/v1/knowledge-bases
GET  /api/v1/knowledge-bases/{knowledge_base_id}
DELETE /api/v1/knowledge-bases/{knowledge_base_id}
GET  /api/v1/documents?knowledge_base_id={id}
```

阶段 2 使用 `JWT_SECRET_KEY` 签发短期 Access Token。开发环境可以使用默认开发值，生产环境必须配置至少 32 个字符的随机密钥。

## 阶段 3 文档解析和入库

阶段 3 支持 Markdown、Markdown 扩展名和 TXT。PDF、Word 已注册统一解析器接口，但当前会进入 `FAILED/PARSER_NOT_IMPLEMENTED`，不会把二进制内容误当成文本处理。

先执行增量迁移：

```powershell
uv run alembic upgrade head
```

上传接口返回 `ingestion_job_id`，请求必须携带 JWT。可使用 `multipart/form-data` 的 `file` 字段，也可以直接提交文件内容并用 URL 查询参数 `file_name` 指定文件名。接口响应统一声明 UTF-8，避免 Windows PowerShell 按系统代码页错误显示中文：

```text
POST /api/v1/knowledge-bases/{knowledge_base_id}/documents
GET  /api/v1/ingestion-jobs/{job_id}
```

上传后由独立 Worker 执行解析、切块、千问 Embedding 和 OpenSearch Bulk upsert。multipart 上传使用标准 `filename` 字段，支持中文文件名；如果直接提交原始文件内容，则使用 URL 查询参数 `file_name`，不要把中文文件名放进自定义 Header。开发期可以手动执行：

```powershell
uv run python -m app.cli process-ingestion-job --job-id <job-id>
uv run python -m app.cli process-next-ingestion-job
```

同一知识库内相同 SHA-256 内容会幂等返回已有文档版本和任务。Chunk ID 由文档版本和序号确定，重试会覆盖同一 ID；Embedding 返回数量或维度不是 `1024` 时任务失败且不会调用 OpenSearch。

## 阶段 4 OpenSearch 索引和检索

先初始化物理索引和两个 Alias。该命令会创建或校验 `rag_chunks_v1`，确认 IK 分析器和 `knn_vector` 维度为 `1024`，再让 `rag_chunks_read`、`rag_chunks_write` 指向该物理索引：

```powershell
uv run python -m app.cli init-opensearch
```

入库 Bulk 请求强制要求写入 Alias；如果 Alias 尚未初始化，任务会失败而不会自动创建同名物理索引。读写 Alias 的切换在一次 `_aliases` 请求中完成。

阶段 3 中处于 `INDEXING`/`RETRY_WAITING` 的任务，在 Alias 创建后可重新执行。开发期检索接口会同时执行独立的 BM25 和 k-NN 查询：

```text
POST /api/v1/retrieval/search
```

如果任务此前已经在错误的物理索引中完成并显示为 `READY`，修复 Alias 后使用 `reindex-ingestion-job` 重新写入正确的写 Alias：

```powershell
uv run python -m app.cli reindex-ingestion-job --job-id <job-id>
uv run python -m app.cli process-ingestion-job --job-id <job-id>
```

请求体必须包含 `knowledge_base_id` 和 `query`；管理员也不能省略知识库 ID。个人用户的 `owner_id` 过滤会同时应用于 Dense 和 Sparse 查询。

## 阶段 5 并行检索、融合和缓存

先执行增量迁移：

```powershell
uv run alembic upgrade head
```

阶段 5 使用共享 Deadline 并行执行 Dense/Sparse，使用 RRF v1 融合，并对 Embedding、Dense、Sparse 分别执行熔断。Redis Cache Aside 缓存 Query Embedding 和检索结果；Redis 不可用时旁路主链路并记录 `CACHE_UNAVAILABLE`。响应中的 `fused` 是 RRF 结果，`status` 为 `completed` 或 `degraded`。

```json
{
  "query": "人工智能与就业",
  "knowledge_base_id": "你的知识库 ID",
  "dense_k": 5,
  "sparse_k": 5
}
```

最终答案缓存暂留到阶段 6 Chat 链路。

## 阶段 6 Chat 和 SSE

问答接口为 `POST /api/v1/chat/completions`，请求需携带 JWT，使用 `Accept: text/event-stream`。请求体包含 `query`、`knowledge_base_id`，可选 `conversation_id`、`dense_k` 和 `sparse_k`。服务端先检索，再调用 `OPENAI_BASE_URL/chat/completions`，通过 SSE 返回 `start`、`citation`、`delta`、`end` 或 `error`；POST 流推荐使用 Fetch API 读取 `ReadableStream`。

`dense`、`sparse`、`fused` 的 `source` 默认不返回 `content_vector`，保留文本和元数据，排名及分数字段继续返回。重启后端后生效，无需重建索引或执行数据库迁移。检索结果缓存改用 `v2`，首次请求可能重新检索；旧 `v1` 缓存自然过期，无需手动清理，Embedding 缓存仍可复用。

## 常用检查

```powershell
uv run ruff check app
uv run pytest
```
