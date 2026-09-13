# Asphoif RAG 前端测试控制台

## 启动

```powershell
cd frontend
npm install
$env:VITE_API_BASE_URL="http://127.0.0.1:8000"
npm run dev
```

打开 Vite 输出的地址，使用后端已创建的账号登录。后端必须允许来自开发前端地址的 CORS 请求；若尚未配置 CORS，可通过同源反向代理或在 FastAPI 增加开发环境 CORS 配置。

当前版本提供登录、知识库创建/切换、文档上传与入库任务轮询、检索调试、Chat SSE 流式问答和 Trace 摘要视图，用于验证阶段 5～7 核心链路。Trace 历史查询待后端提供专用查询接口后接入。
