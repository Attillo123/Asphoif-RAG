# Docker Compose 部署

Compose 部署只运行前端、FastAPI API、入库 Worker 和迁移初始化服务。MySQL、Redis、OpenSearch 使用 `.env` 中已经配置的服务器地址。API 和 Worker 使用同一个镜像；`migrate` 服务执行 Alembic 迁移并初始化 `rag_chunks_v1` 及读写 Alias。

## 服务器首次部署

```bash
cp .env.docker.example .env
# 将 DATABASE_URL、REDIS_URL、OPENSEARCH_URL 改成服务器已有服务地址
# 再替换 *_API_KEY 和 JWT_SECRET_KEY
docker compose build
docker compose up -d
docker compose ps
```

前端默认监听服务器 `80` 端口。存活检查为 `http://服务器地址/health/live`，依赖就绪检查为 `http://服务器地址/health/ready`。

首次创建管理员：

```bash
docker compose run --rm api python -m app.cli create-user \
  --username admin --password 'change-this-password' --role admin
```

日志和运维命令：

```bash
docker compose logs -f api worker
docker compose exec api python -m app.cli init-opensearch
docker compose down                 # 保留命名卷
docker compose down -v              # 删除数据库、缓存、索引和文件卷
```

不要把生产密钥提交到 Git。Compose 不会创建或管理外部 MySQL、Redis、OpenSearch 容器。
