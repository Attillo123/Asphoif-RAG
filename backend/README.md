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

## 常用检查

```powershell
uv run ruff check app
uv run pytest
```
