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

## 常用检查

```powershell
uv run ruff check app
uv run pytest
```
