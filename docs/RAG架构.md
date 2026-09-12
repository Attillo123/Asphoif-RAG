# 企业级 RAG 系统设计架构

## 1. 文档定位

本文定义企业级 RAG 系统的组件边界、数据流、接口契约和工程约束。

本文覆盖：

- 知识入库、解析、切分、索引和版本管理；
- 在线问答：Query 理解、缓存、权限、混合检索、Rerank、上下文构造和 LLM 生成；
- 个人/管理员权限、资源所有权隔离、可观测性、异常降级和成本控制；
- 与 `RAG优化链路.md`、`RAG缓存设计.md`、`RAG评估.md` 的衔接；
- 第一阶段的技术选型和后续演进路线。

缓存是在线链路的加速层，不是事实源；评估是 RAG 主链路完成后的质量保障层，不应耦合进在线请求的核心逻辑。

## 2. 设计目标与非目标

### 2.1 设计目标

1. 检索结果可追溯：每个回答都能关联知识库、文档版本、Chunk 和引用位置。
2. 权限先于内容：用户只能检索和看到有权访问的知识，缓存不能绕过权限校验。
3. 检索可替换：向量库、全文检索、Embedding、Reranker 和 LLM 均通过适配器接入。
4. 链路可降级：组件超时或异常时返回可解释的降级结果，禁止静默编造。
5. 数据可复现：可以恢复一次回答使用的 Prompt、模型、检索策略和知识库版本。
6. 前后端分离：前端只依赖稳定的 HTTP/SSE 接口，后端负责鉴权、编排和模型调用。
7. 渐进式建设：首期采用模块化单体，后续只拆分已确认的高负载模块。

### 2.2 非目标

- 首期不建设通用工作流平台、模型训练平台或完整实时数仓。
- 首期不要求所有阶段都缓存，也不默认引入复杂事件总线或主动预热系统。
- RAG 不替代业务系统的事务查询。实时库存、订单或审批状态应通过受控业务 API/结构化查询获取，再由 LLM 解释。

## 3. 总体架构

```text
                         ┌─────────────────────────────┐
                         │        Web / 管理前端         │
                         │  对话、引用、知识库、评估、监控  │
                         └──────────────┬──────────────┘
                                        │ HTTPS / SSE
                         ┌──────────────▼──────────────┐
                         │          API Gateway          │
                         │ 认证、限流、Request ID、审计入口 │
                         └──────────────┬──────────────┘
                                        │
              ┌─────────────────────────▼────────────────────────┐
              │             FastAPI RAG Application               │
              │ Auth/Role/Ownership · Query Router · Cache Aside  │
              │ Retrieval · Context · Citation · OpenAI Adapter   │
              └─────────┬─────────────┬───────────────┬────────────┘
                        │             │               │
              ┌─────────▼──────┐ ┌───▼───────────┐ ┌─▼──────────────┐
              │ Redis Cache     │ │ MySQL         │ │ OpenAI-compatible│
              │ 缓存、锁、热度、限流 │ │ 元数据、用户、任务 │ │ Embed/Rerank/LLM │
              └────────────────┘ └───────────────┘ └────────────────┘
                        │
              ┌─────────▼─────────────────────────────────────────┐
              │ Retrieval Orchestrator: Dense + Sparse + RRF      │
              └────────────┬──────────────────────┬───────────────┘
                           │                      │
              ┌───────────────────────────────────────────────────┐
              │ OpenSearch                                        │
              │ k-NN Dense + BM25 Sparse + 元数据过滤             │
              └───────────────────────────────────────────────────┘

  文档上传 -> Local File Storage -> Ingestion Worker -> Parser/Chunker
                                                -> Embedding/Indexer
```

### 3.1 技术基线

| 层级 | 首期选择 | 说明 |
| --- | --- | --- |
| 语言 | Python 3.10 | 统一运行时版本并锁定依赖 |
| API | FastAPI + Pydantic | REST 管理接口；问答使用 SSE 流式返回 |
| 包管理/部署 | `uv` | 使用 `pyproject.toml` 和锁文件，CI 与本地结果一致 |
| 关系数据库 | MySQL 8.x + SQLAlchemy 2.x | 用户、角色、知识库、文档、任务、审计和评估结果 |
| 缓存 | Redis | Cache Aside；缓存、分布式锁、热点统计和限流计数 |
| 文件存储 | 本地存储适配器 | 测试期保存少量上传文件；接口预留 S3 兼容实现 |
| 统一检索引擎 | OpenSearch | 同时负责 k-NN Dense、BM25 Sparse、字段权重和元数据过滤 |
| 异步任务 | Worker 抽象，可接 Celery/RQ/消息队列 | 文档解析和索引不能占用 API 进程 |
| 模型接入 | Chat 使用 OpenAI-compatible API；Embedding 使用千问 API | Chat、Embedding 和可选 Rerank 分别配置模型名、Base URL 和 API Key |
| 观测 | 结构化日志、Metrics、Trace | 记录阶段耗时、失败、Token、成本和版本 |

### 3.2 MySQL 与 OpenSearch 边界

MySQL 是系统事实源，负责用户、角色、文档、版本、任务和对话状态。OpenSearch 是统一检索引擎，保存 Chunk 的文本、向量和过滤元数据。OpenSearch 不能代替 MySQL，文档和权限的权威状态仍以 MySQL 为准。

检索层分别定义 `DenseRetriever` 和 `SparseRetriever` 接口，由应用层 `HybridRetriever` 并发调用同一个 OpenSearch 集群中的 k-NN 和 BM25 查询，并使用 RRF 融合。首期不使用 OpenSearch Hybrid DSL，以便分别记录 Dense/Sparse 的耗时、异常、熔断和降级状态。

测试期只有几个文档，OpenSearch 以单节点开发模式运行即可。进入多实例部署后，再根据 Chunk 数、QPS 和 P95 延迟配置分片、副本、内存和 k-NN 索引参数。

### 3.3 OpenAI-compatible API 约束

模型客户端按能力隔离配置：Chat 使用通用 OpenAI-compatible API，Embedding 首期使用千问 Embedding API。即使千问接口兼容 OpenAI SDK，也不要让 Chat 和 Embedding 共用同一组环境变量，避免模型、密钥或供应商切换时互相影响。

- Chat：使用 `OPENAI_BASE_URL`、`OPENAI_API_KEY` 和 `CHAT_MODEL`，调用 `/chat/completions`，支持流式输出；
- Embedding：使用 `QWEN_EMBEDDING_BASE_URL`、`QWEN_EMBEDDING_API_KEY` 和 `QWEN_EMBEDDING_MODEL`，调用兼容 `/embeddings` 的接口；
- Rerank：OpenAI API 没有统一标准接口，只有供应商提供兼容 endpoint 时才启用；否则使用 RRF/融合分数初排。

不同模型的超时、重试、Token 上限、向量维度和响应解析由 Adapter 统一处理，业务服务不直接拼装供应商请求。Embedding 模型或维度发生变化时，必须创建新的 OpenSearch 索引版本，不能直接覆盖旧向量字段。

## 4. 核心领域模型

### 4.1 MySQL 实体

| 实体 | 关键字段 | 作用 |
| --- | --- | --- |
| `users` | `id`, `username`, `password_hash`, `role`, `status` | 本地身份；`role` 为 `personal` 或 `admin` |
| `knowledge_bases` | `id`, `owner_id`, `current_version`, `status`, `deleted_at` | 所有者隔离、版本入口和软删除 |
| `documents` | `id`, `knowledge_base_id`, `source_uri`, `content_hash`, `status` | 原始文档状态 |
| `document_versions` | `id`, `document_id`, `version`, `parser_version` | 可复现文档版本 |
| `chunks` | `id`, `document_version_id`, `content_hash`, `ordinal`, `start_offset`, `metadata` | 最小证据单元 |
| `ingestion_jobs` | `id`, `document_version_id`, `stage`, `status`, `error` | 入库任务、重试和失败原因 |
| `conversation/message` | `user_id`, `content`, `metadata` | 对话和反馈关联 |
| `query_traces` | `request_id`, `versions`, `latency`, `token_usage`, `cost` | 线上追踪和审计索引 |
| `evaluation_*` | 数据集、运行配置、Case 指标 | 离线评估结果复现 |

约束：

- 所有用户资源必须带 `owner_id` 或可追溯到所属知识库，并通过统一 Repository 注入所有权条件。
- `personal` 只能读写自己的知识库、文档、任务、对话和反馈；`admin` 可以读写全部资源并管理用户。
- 首期不实现租户、部门、密级或 Chunk ACL，但所有权检查必须发生在数据库查询和检索过滤两个位置。
- 业务主键使用不可预测 ID（UUID/ULID）；外部 API 不暴露内部自增 ID。
- `content_hash` 保证幂等入库；相同内容不重复解析和生成 Embedding。
- MySQL 的 Chunk 是事实记录，检索引擎保存召回副本，二者通过 `chunk_id` 和版本对齐。

### 4.2 OpenSearch 统一索引

OpenSearch 保存同一个索引文档中的向量、文本和过滤元数据：

```jsonc
{
  "chunk_id": "chunk_01J...",
  "owner_id": "user_001",
  "knowledge_base_id": "kb_001",
  "knowledge_base_version": 12,
  "document_id": "doc_001",
  "document_version": 3,
  "title": "费用报销制度",
  "content": "...",
  "content_vector": [0.01, -0.02],
  "embedding_model": "text-embedding-model",
  "metadata": {},
  "index_version": "rag_chunks_v1",
  "retrieval_version": "app-hybrid-v1"
}
```

索引必须能按 `owner_id`、知识库 ID 和版本过滤。`personal` 请求固定过滤 `owner_id == current_user.id`；`admin` 可以不加所有者过滤，但仍必须限制请求指定的知识库。检索结果返回应用层后，再根据 MySQL 中的资源所有者二次校验。

### 4.3 OpenSearch mapping 与开发期查询契约

首期索引只保留一套 OpenSearch 索引。原始 Chunk 文本使用 `text`，向量使用 `knn_vector`，用于权限、知识库和版本过滤的元数据使用 `keyword`。`content` 不显式指定 `similarity`，因此使用 OpenSearch 默认 BM25。

下面是开发期 mapping 模板。首期千问 Embedding 维度固定为 `1024`；应用启动和入库时都必须校验模型实际返回的向量长度。后续维度变化时必须创建新的物理索引版本：

```jsonc
{
  "settings": {
    "index.knn": true,
    "number_of_shards": 1,
    "number_of_replicas": 0,
    "analysis": {
      "analyzer": {
        "rag_index_analyzer": {
          "type": "custom",
          "tokenizer": "ik_max_word"
        },
        "rag_search_analyzer": {
          "type": "custom",
          "tokenizer": "ik_smart"
        }
      }
    }
  },
  "mappings": {
    "dynamic": "strict",
    "properties": {
      "chunk_id": { "type": "keyword" },
      "owner_id": { "type": "keyword" },
      "knowledge_base_id": { "type": "keyword" },
      "knowledge_base_version": { "type": "keyword" },
      "document_id": { "type": "keyword" },
      "document_version": { "type": "keyword" },
      "title": {
        "type": "text",
        "analyzer": "rag_index_analyzer",
        "search_analyzer": "rag_search_analyzer"
      },
      "content": {
        "type": "text",
        "analyzer": "rag_index_analyzer",
        "search_analyzer": "rag_search_analyzer"
      },
      "content_vector": {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
          "name": "hnsw",
          "engine": "lucene",
          "space_type": "cosinesimil"
        }
      },
      "embedding_model": { "type": "keyword" },
      "index_version": { "type": "keyword" },
      "metadata": {
        "type": "object",
        "dynamic": "strict",
        "properties": {
          "source_type": { "type": "keyword" },
          "file_name": { "type": "keyword", "ignore_above": 256 },
          "section_path": { "type": "keyword", "ignore_above": 512 },
          "tags": { "type": "keyword", "ignore_above": 256 },
          "language": { "type": "keyword" }
        }
      }
    }
  }
}
```

开发期只做关键词查询和向量查询，由应用层并行调用同一个 OpenSearch 索引。默认分别设置 `dense_k=5`、`sparse_k=5`、`final_top_k=5`；三个值独立配置，后续通过评估分别调整。

索引和 Alias 固定如下：

```text
rag_chunks_v1       # 开发期物理索引
rag_chunks_read     # 线上检索读取 Alias
rag_chunks_write    # 入库写入 Alias
```

线上读取只访问 `rag_chunks_read`，入库写入只访问 `rag_chunks_write`。新 Embedding 模型、向量维度、分词器或 mapping 变化时创建新物理索引，完成校验后在一次 `_aliases` 请求中原子切换两个 Alias；不直接修改线上索引 mapping。Bulk 入库和删除请求必须设置 `require_alias=true`，防止 Alias 未初始化时被 OpenSearch 自动创建同名物理索引。

阶段 4 的索引初始化由 `init-opensearch` CLI 执行：物理索引不存在时创建，存在时校验 mapping、向量维度和 IK 分析器；校验通过后以 `_aliases` 原子设置读写 Alias。初始化不会自动覆盖不兼容的已有 mapping。

下面分别展示应用层发送给 OpenSearch 的关键词和向量查询体；两次请求并行发送，下面的代码不是 OpenSearch `hybrid` DSL：

```jsonc
// 关键词请求：size = sparse_k
POST rag_chunks_read/_search
{
  "size": 5,
  "_source": { "excludes": ["content_vector"] },
  "query": {
    "bool": {
      "must": [{ "match": { "content": "退款多久到账？" } }],
      "filter": [
        { "term": { "owner_id": "user_001" } },
        { "term": { "knowledge_base_id": "kb_001" } },
        { "term": { "knowledge_base_version": "12" } }
      ]
    }
  }
}

// 向量请求：k = dense_k
POST rag_chunks_read/_search
{
  "size": 5,
  "_source": { "excludes": ["content_vector"] },
  "query": {
    "knn": {
      "content_vector": {
        "vector": [0.01, -0.02],
        "k": 5,
        "filter": {
          "bool": {
            "filter": [
              { "term": { "owner_id": "user_001" } },
              { "term": { "knowledge_base_id": "kb_001" } },
              { "term": { "knowledge_base_version": "12" } }
            ]
          }
        }
      }
    }
  }
}
```

代码块中的两个请求体由应用层并行发送，分别设置 `size=sparse_k` 与 `k=dense_k`。关键词查询使用 OpenSearch 默认 BM25；索引时使用 `ik_max_word` 细粒度切分，搜索时使用 `ik_smart` 粗粒度切分；应用层使用 RRF 生成 `final_top_k`。查询条件、Alias、分词器、过滤条件和三个 K 值写入 `retrieval_version`，保证后续评估可复现。部署前必须确认 IK 插件已安装；未安装时索引创建应失败，不能静默退回 `standard`。

阶段 4 提供开发期检索接口 `POST /api/v1/retrieval/search`。它先调用千问 Embedding，再由应用层并行发起 Sparse 和 Dense 请求，返回两路原始候选；RRF 融合、超时、熔断、降级和缓存属于阶段 5。请求必须指定 `knowledge_base_id`，个人请求在两路 DSL 中加入 `owner_id`，管理员请求不加入所有者条件但仍限制知识库。

检索响应默认排除 `source.content_vector`，保留文本、元数据、排名和分数；阶段 5 的融合结果同时保留召回通道及各路排名和分数。两路查询通过 `_source.excludes` 过滤返回字段，不影响索引中的向量存储和 k-NN 计算。检索结果缓存格式使用 `v2`，避免命中包含向量的旧 `v1` 缓存；Embedding 缓存仍保存向量。

### 4.4 评估、运行清单和线上 Trace 数据表

这些表先承担数据契约和可回放能力，评估指标计算放到后续阶段。所有 ID 使用 UUID/ULID 字符串，JSON 字段必须通过对应 Schema 校验。评估快照发布后不可修改，修改必须生成新快照版本。

数据库表统一使用复数命名：`users`、`knowledge_bases`、`documents`、`document_versions`、`chunks`、`ingestion_jobs`、`evaluation_dataset_snapshots`、`evaluation_dataset_cases`、`evaluation_runs`、`evaluation_case_results` 和 `query_traces`。字段名中的单数语义，例如 `document_id`、`document_version_id` 和 `evaluation_run_id`，保持不变。

```sql
CREATE TABLE evaluation_dataset_snapshots (
    snapshot_id CHAR(26) NOT NULL,
    dataset_id CHAR(26) NOT NULL,
    snapshot_version VARCHAR(32) NOT NULL,
    name VARCHAR(128) NOT NULL,
    status VARCHAR(16) NOT NULL,
    knowledge_base_id CHAR(26) NULL,
    knowledge_base_version VARCHAR(64) NULL,
    source_type VARCHAR(32) NOT NULL,
    dataset_sha256 CHAR(64) NOT NULL,
    case_count INT UNSIGNED NOT NULL DEFAULT 0,
    schema_version VARCHAR(32) NOT NULL,
    metadata JSON NOT NULL,
    created_by CHAR(26) NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (snapshot_id),
    UNIQUE KEY uq_dataset_snapshot (dataset_id, snapshot_version),
    KEY idx_dataset_kb (knowledge_base_id, knowledge_base_version)
);

CREATE TABLE evaluation_dataset_cases (
    case_id CHAR(26) NOT NULL,
    snapshot_id CHAR(26) NOT NULL,
    ordinal INT UNSIGNED NOT NULL,
    question TEXT NOT NULL,
    reference_answer TEXT NULL,
    gold_chunk_ids JSON NOT NULL,
    question_type VARCHAR(32) NOT NULL,
    is_unanswerable BOOLEAN NOT NULL DEFAULT FALSE,
    requires_citation BOOLEAN NOT NULL DEFAULT TRUE,
    allowed_answers JSON NULL,
    annotations JSON NOT NULL,
    case_sha256 CHAR(64) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (case_id),
    UNIQUE KEY uq_snapshot_case_ordinal (snapshot_id, ordinal),
    KEY idx_case_snapshot (snapshot_id),
    CONSTRAINT fk_case_snapshot FOREIGN KEY (snapshot_id)
      REFERENCES evaluation_dataset_snapshots (snapshot_id)
);

CREATE TABLE evaluation_runs (
    run_id CHAR(26) NOT NULL,
    snapshot_id CHAR(26) NOT NULL,
    status VARCHAR(16) NOT NULL,
    run_manifest JSON NOT NULL,
    aggregate_metrics JSON NULL,
    error_message TEXT NULL,
    created_by CHAR(26) NULL,
    started_at DATETIME(6) NULL,
    finished_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (run_id),
    KEY idx_run_snapshot (snapshot_id),
    KEY idx_run_status (status),
    CONSTRAINT fk_run_snapshot FOREIGN KEY (snapshot_id)
      REFERENCES evaluation_dataset_snapshots (snapshot_id)
);

CREATE TABLE evaluation_case_results (
    result_id CHAR(26) NOT NULL,
    run_id CHAR(26) NOT NULL,
    case_id CHAR(26) NOT NULL,
    request_id CHAR(26) NULL,
    status VARCHAR(16) NOT NULL,
    retrieved_chunk_ids JSON NOT NULL,
    context_snapshot JSON NULL,
    answer TEXT NULL,
    citations JSON NULL,
    metrics JSON NOT NULL,
    diagnosis JSON NULL,
    latency_ms INT UNSIGNED NULL,
    input_tokens INT UNSIGNED NULL,
    output_tokens INT UNSIGNED NULL,
    cost DECIMAL(18,8) NULL,
    error_message TEXT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (result_id),
    UNIQUE KEY uq_run_case (run_id, case_id),
    KEY idx_result_request (request_id),
    CONSTRAINT fk_result_run FOREIGN KEY (run_id) REFERENCES evaluation_runs (run_id),
    CONSTRAINT fk_result_case FOREIGN KEY (case_id) REFERENCES evaluation_dataset_cases (case_id)
);

CREATE TABLE query_traces (
    request_id CHAR(26) NOT NULL,
    trace_schema_version VARCHAR(32) NOT NULL,
    trace_type VARCHAR(16) NOT NULL,
    evaluation_run_id CHAR(26) NULL,
    evaluation_case_id CHAR(26) NULL,
    user_id CHAR(26) NULL,
    role VARCHAR(16) NULL,
    conversation_id CHAR(26) NULL,
    knowledge_base_id CHAR(26) NULL,
    knowledge_base_version VARCHAR(64) NULL,
    query_hash CHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
    degraded BOOLEAN NOT NULL DEFAULT FALSE,
    deadline_ms INT UNSIGNED NOT NULL,
    deadline_started_at DATETIME(6) NOT NULL,
    deadline_ended_at DATETIME(6) NULL,
    timeout_at DATETIME(6) NULL,
    degraded_at DATETIME(6) NULL,
    total_latency_ms INT UNSIGNED NULL,
    first_token_latency_ms INT UNSIGNED NULL,
    input_tokens INT UNSIGNED NULL,
    output_tokens INT UNSIGNED NULL,
    cost DECIMAL(18,8) NULL,
    error_code VARCHAR(64) NULL,
    manifest JSON NOT NULL,
    stages JSON NOT NULL,
    timeout_summary JSON NOT NULL,
    retrieval_snapshot JSON NULL,
    output_snapshot JSON NULL,
    degraded_reasons JSON NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (request_id),
    KEY idx_trace_created (created_at),
    KEY idx_trace_user (user_id, created_at),
    KEY idx_trace_kb (knowledge_base_id, knowledge_base_version),
    KEY idx_trace_run (evaluation_run_id, evaluation_case_id)
);
```

设计取舍：`query_traces` 保存评估需要的运行清单、阶段耗时、召回快照、上下文和输出快照；生产环境可按保留周期脱敏或归档正文。`evaluation_case_results` 保存每个 Case 的结果和指标，避免每次评估都重新解析线上 Trace。候选 Chunk 先放在 `retrieval_snapshot` JSON 中，只有在数据量明显增大后再拆分为明细表。

JSON 数据契约位于 `docs/schemas/`：

- `rag-run-manifest.schema.json`：一次评估运行的不可变配置；
- `rag-dataset-snapshot.schema.json`：固定问题集与标准证据；
- `rag-query-trace.schema.json`：线上、回放和评估请求的 Trace；
- `rag-evaluation-case-result.schema.json`：单个评估 Case 的输出、指标与诊断；
- `api-response.schema.json`：普通 API 的统一成功/错误响应；
- `sse-event.schema.json`：流式事件外壳和终止事件约束。

## 5. 离线知识入库链路

```text
本地上传文档（未来可增加同步 Connector）
  -> 计算 content_hash 并记录 owner_id
  -> 创建 document_versions 与 ingestion_jobs
  -> 本地文件存储保存原文件
  -> 文档解析（格式、编码、表格、图片、OCR）
  -> 清洗和结构化（标题层级、页码、来源位置）
  -> Chunk 切分（带重叠、标题路径和引用坐标）
  -> 生成 Embedding
  -> 写入 OpenSearch k-NN 向量字段与 BM25 文本字段
  -> 一致性校验
  -> 原子发布 knowledge_base_version
```

### 5.1 入库原则

1. 解析、切分、Embedding 和索引写入均须可重试、可观测、可幂等。
2. 新版本索引通过校验后才更新当前版本，线上请求不能看到半成品索引。
3. 新增或修改文档按内容哈希增量处理，避免全量重算。
4. 删除采用软删除与索引删除两阶段流程，保留审计记录。
5. Chunk 必须保留文档 ID、版本、标题、页码/偏移，用于引用和评估。
6. 图片、表格和扫描件尽量保留结构化结果和原始位置，不只保存 OCR 纯文本。

### 5.2 发布状态与一致性

- `BUILDING`：正在解析或构建，不能供线上使用；
- `READY`：索引完整且校验通过；
- `PUBLISHED`：成为当前线上版本；
- `FAILED`：记录失败阶段和可重试原因。

发布成功后更新 Redis 中的知识库版本标识。检索和答案缓存通过版本自然失效，旧索引保留一段时间用于回滚和评估复现。

### 5.3 最小入库任务状态机

首期每个文档版本对应一条 `ingestion_jobs` 记录，任务按以下状态推进：

```text
PENDING
  -> PARSING
  -> CHUNKING
  -> EMBEDDING
  -> INDEXING
  -> READY
  -> PUBLISHED
```

任意处理状态都可以进入 `RETRY_WAITING`，超过最大重试次数后进入 `FAILED`。`READY` 表示 Chunks 已写入 `rag_chunks_write` 指向的物理索引并通过数量、字段和向量维度校验；只有管理员发布后才进入 `PUBLISHED`，并原子切换 `rag_chunks_read` Alias。

| 状态 | 进入条件 | 最小动作 |
| --- | --- | --- |
| `PENDING` | 创建文档版本 | 校验文件存在、大小和类型 |
| `PARSING` | Worker 领取任务 | 解析文本、标题、页码和来源位置 |
| `CHUNKING` | 解析成功 | 生成稳定 Chunk ID、内容哈希和所有者元数据 |
| `EMBEDDING` | 切分成功 | 调用千问 Embedding，校验维度和数量 |
| `INDEXING` | 向量准备完成 | 批量写入 OpenSearch，并使用 `owner_id`、版本和 `index_version` |
| `READY` | 索引校验成功 | 等待发布；线上仍使用旧版本 |
| `PUBLISHED` | 管理员或发布流程确认 | 切换读 Alias，更新知识库当前版本 |
| `RETRY_WAITING` | 可恢复错误 | 记录错误、重试次数和下次执行时间 |
| `FAILED` | 不可恢复或重试耗尽 | 保留错误和失败阶段，不切换 Alias |

任务必须幂等：同一 `document_version_id` 重试时使用相同 Chunk ID 和索引文档 ID；写入采用批次记录，重试前允许覆盖同一 ID。发布前检查期望 Chunk 数与实际写入数一致，且所有文档都带同一知识库版本。首期不引入复杂队列，Worker 可从 MySQL 查询 `PENDING`/`RETRY_WAITING` 任务并使用数据库锁领取。

首期实现约束：上传 API 只负责文件大小/扩展名校验、原文件保存和创建任务；解析、切块、Embedding 与 OpenSearch Bulk 写入由独立 Worker 执行。文本文件按 UTF-8 或 GB18030 解码，Markdown 保留标题路径，TXT 按段落解析。PDF/Word 只注册 `DocumentParser` 接口，当前任务以 `PARSER_NOT_IMPLEMENTED` 失败，不会把二进制内容送入文本解析器。开发期 Worker 可通过 CLI 手动领取单个任务或处理指定任务。

## 6. 在线问答链路

```text
请求进入
  -> JWT 认证、角色解析、资源所有权检查、参数校验
  -> Query 规范化、语言识别、意图和复杂度判断
  -> 答案缓存
       ├─ 版本、权限、引用校验通过 -> 返回
       └─ 未命中 -> 继续
  -> FAQ / 结构化查询路由
  -> Embedding 与 owner_id 过滤条件准备
  -> Dense、Sparse、字段和业务规则并行召回
  -> RRF 融合、去重、所有权二次校验、相邻 Chunk 合并
  -> 条件式 Rerank
  -> 上下文压缩、冲突检测、Token 预算控制
  -> 选择 OpenAI-compatible Chat 模型并流式生成
  -> 引用绑定与安全校验
  -> SSE 返回答案、引用、状态和 request_id
  -> 异步记录 Trace、反馈和指标，并按策略写缓存
```

### 6.1 Query 理解与路由

- 去除首尾空白，统一全角半角、大小写和标点；
- 检查长度、语言、敏感词、提示注入和明显非法输入；
- 识别事实、总结、多跳、比较、条件判断、实时查询和不可回答问题；
- 必要时做 Query Rewrite，同时保存原 Query、改写 Query 和原因；
- 简单 FAQ 或结构化业务查询走专用路径，避免不必要的大模型调用。

改写不能改变用户意图或绕过权限。高风险场景保留原 Query 检索作为兜底。

### 6.2 身份、角色与所有权

首期采用本地账号、密码哈希和短期 JWT Access Token，不实现租户、部门、密级和复杂 ACL：

1. `personal`：只能创建、读取、更新和删除自己的知识库、文档、任务、对话和反馈。
2. `admin`：可以管理用户，并读取或管理所有用户资源。
3. API 层先验证 JWT 和角色；Repository 查询按 `owner_id` 过滤；OpenSearch 的 Dense 和 Sparse 查询使用相同的 `owner_id` 条件。
4. 检索结果返回后，用 MySQL 中的知识库所有者做二次校验，生成和引用阶段不得绕过。

密码使用 Argon2id 或 bcrypt 哈希，JWT 密钥从环境变量注入。后续接入 OIDC/IAM 时替换认证适配器，不改变 `owner_id` 资源授权模型。

### 6.3 混合检索

首期默认并行执行：

- Dense retrieval：Query Embedding 语义召回；
- Sparse retrieval：BM25、标题和关键词字段召回；
- Metadata filtering：`owner_id`、知识库、版本和可选业务标签；
- Business retrieval：FAQ、实体、产品编码或受控业务 API。

各路结果使用 RRF 或可配置加权融合，之后按 `chunk_id` 去重、合并相邻 Chunk、过滤低分结果，并记录每个候选的召回通道和原始分数。阶段 5 首期固定实现 RRF v1，`rrf_k=60`，结果截断到 `final_top_k`；Dense、Sparse 和 Embedding 分别受独立熔断器保护，Dense/Sparse 共用检索 Deadline。

融合权重、候选数、最低分数和 Rerank 阈值必须配置化并写入 `retrieval_version`，以支持离线评估和线上回放。

### 6.4 条件式 Rerank 与上下文

1. 轻量模型或融合分数对较大候选集初排。
2. 仅对低置信度、高价值、复杂或需要精确引用的问题调用高质量 Reranker。

Rerank 超时时退化到融合初排结果。上下文构造需要：

- 按证据分数、来源权威性和新鲜度排序；
- 去除重复句子和高度重叠 Chunk；
- 保留标题路径、页码、来源和版本；
- 压缩长文档时保留限定条件、数字和否定词；
- 检测证据冲突，要求模型说明适用范围；
- 受 `max_context_tokens` 限制，截断时记录原因。

### 6.5 生成、引用与拒答

Prompt 必须要求模型只基于证据回答，证据不足时明确拒答；事实陈述必须绑定引用；多个版本或冲突来源要说明时间和适用范围；检索内容不能覆盖系统指令。

生成后执行 Citation Validator 和安全检查：

- 引用的 `chunk_id` 必须存在于本次检索结果；
- 引用必须属于当前用户可访问的知识库版本；
- 重要结论需要证据支持；
- 检测敏感信息、越权内容、危险建议和明显幻觉。

流式阶段尽早返回首 Token，但最终状态必须是 `completed`、`degraded` 或 `failed`，错误不能拼接成看似正常的答案。

## 7. 缓存架构

Redis 使用 Cache Aside，是可绕过的加速层：

```text
L1 answer      -> 稳定、低风险、权限明确的最终答案
L2 retrieve    -> 检索结果、候选 Chunk 和检索版本
L3 embedding   -> 规范化 Query 向量
L4 rerank      -> 后续按收益增加
```

### 7.1 Key 与失效

```text
rag:{env}:{layer}:{format_version}:{scope}:{sha256(parameters)}
```

哈希参数包含所有影响结果的字段：`owner_id`（管理员代查时为目标资源所有者）、知识库及其版本、角色范围、规范化 Query、过滤条件、`top_k`、Embedding/索引/检索/Rerank/Prompt/模型版本。

知识库发布或权限版本变更时通过版本切换自然失效，不扫描 Redis 删除大量 Key。TTL 作为兜底并添加随机抖动。

### 7.2 初始策略

| 缓存层 | 首期 | 建议 TTL | 说明 |
| --- | --- | --- | --- |
| Query embedding | 启用 | 1-3 天 | 优先缓存高频 Query，关注内存占用 |
| 检索结果 | 启用 | 10 分钟-1 小时 | 版本和过滤条件必须入 Key |
| Rerank | 可选 | 30 分钟 | 候选与模型版本必须入 Key |
| 最终答案 | 有条件启用 | 10 分钟-24 小时 | 仅稳定、低风险、可共享问题 |
| 无结果/拒答 | 启用 | 1-5 分钟 | 防止重复打穿检索和 LLM |

热点 Key 使用分布式锁或 singleflight；可用逻辑过期和后台刷新降低击穿。答案缓存命中后仍需做版本、权限和引用校验。

阶段 5 的最小实现启用 Embedding 缓存和检索结果缓存，采用 Cache Aside、TTL 抖动和 Redis 短锁。Redis 只作为加速层：读取、写入或锁操作失败时继续主链路并记录 `CACHE_UNAVAILABLE`。最终答案缓存留到 Chat 阶段，评估回放默认关闭。阶段 5 的 Trace 关联字段迁移由 `0004_widen_trace_request_id` 完成。

## 8. 异常、超时与降级

| 阶段 | 异常处理 |
| --- | --- |
| 身份/所有权 | 失败即拒绝，禁止降级为匿名或全量权限 |
| Query Rewrite | 使用原 Query |
| Embedding | 命中缓存则继续，否则退化为 BM25/FAQ |
| Dense | 退化为 Sparse/FAQ，并标记 `degraded` |
| Sparse | 退化为 Dense/FAQ；全部失败则明确不可用 |
| Rerank | 使用融合后的初排结果 |
| 上下文压缩 | 使用未压缩但受 Token 限制的证据 |
| LLM | 返回检索摘要或明确不可用提示，禁止无证据补答 |
| Redis | 继续核心链路，缓存异常不能中断业务 |
| MySQL | 权限/版本不确定时失败关闭，不使用不可信旧权限 |
| 检索引擎 | 熔断并恢复探测，防止级联故障 |

重试只用于可恢复网络错误，采用指数退避和抖动。模型生成默认不盲目重试，避免重复成本和延迟放大。

### 8.1 统一 API 响应与错误码

所有 HTTP API（包括管理接口、非流式问答和流式连接建立前的错误）使用同一个响应结构。HTTP 状态码表达协议层结果，业务 `code` 表达稳定的应用错误类型，调用方应优先依据 `code` 做针对性处理。

成功响应：

```json
{
  "success": true,
  "code": 0,
  "message": "ok",
  "data": {},
  "error": null,
  "request_id": "req_01J...",
  "timestamp": "2026-09-07T12:00:00.000Z"
}
```

错误响应：

```json
{
  "success": false,
  "code": 40101,
  "message": "登录状态已失效",
  "data": null,
  "error": {
    "type": "TOKEN_EXPIRED",
    "retryable": false,
    "details": {}
  },
  "request_id": "req_01J...",
  "timestamp": "2026-09-07T12:00:00.000Z"
}
```

约束：

- `code=0` 只用于成功；错误码一旦发布不得复用或改变语义。
- `message` 是面向用户的安全提示，不能包含堆栈、API Key、Prompt、原始文档或内部地址。
- `error.type` 使用稳定的大写标识，便于前端展示和日志聚合；`details` 只返回调用方确实需要的结构化字段。
- `retryable` 只表示调用方是否可以在相同请求语义下重试，不代表服务端一定会成功。
- 所有响应都必须带 `request_id`；服务端日志、Trace、SSE 和异步任务使用同一个 ID 关联。

错误码按模块分段，首期固定如下：

| 区间 | 模块 | 错误码 | 类型 | HTTP | 可重试 | 处理建议 |
| --- | --- | ---: | --- | ---: | --- | --- |
| 10000-10999 | 通用请求 | 10001 | `INVALID_REQUEST` | 400 | 否 | 修正请求参数 |
|  |  | 10002 | `VALIDATION_ERROR` | 422 | 否 | 根据 `details.fields` 修正字段 |
|  |  | 10003 | `RESOURCE_NOT_FOUND` | 404 | 否 | 检查资源 ID |
|  |  | 10004 | `RATE_LIMITED` | 429 | 是 | 按 `Retry-After` 退避 |
| 20000-20999 | 身份与权限 | 20001 | `TOKEN_INVALID` | 401 | 否 | 重新登录 |
|  |  | 20002 | `TOKEN_EXPIRED` | 401 | 否 | 刷新或重新登录 |
|  |  | 20003 | `ROLE_FORBIDDEN` | 403 | 否 | 检查角色权限 |
|  |  | 20004 | `RESOURCE_FORBIDDEN` | 403 | 否 | 只能访问自己的资源 |
| 30000-30999 | 文档与知识库 | 30001 | `DOCUMENT_TYPE_UNSUPPORTED` | 415 | 否 | 更换支持的文件类型 |
|  |  | 30002 | `DOCUMENT_TOO_LARGE` | 413 | 否 | 压缩或拆分文件 |
|  |  | 30003 | `INGESTION_NOT_READY` | 409 | 是 | 等待入库任务完成 |
|  |  | 30004 | `INDEX_PUBLISH_FAILED` | 500 | 是 | 查询任务状态后重试 |
| 40000-40999 | 检索 | 40001 | `EMBEDDING_UNAVAILABLE` | 503 | 是 | 等待模型恢复或走关键词降级 |
|  |  | 40002 | `RETRIEVAL_UNAVAILABLE` | 503 | 是 | 等待 OpenSearch 恢复 |
|  |  | 40004 | `RETRIEVAL_TIMEOUT` | 503 | 是 | 按退避策略重试 |
| 50000-50999 | 模型生成 | 50001 | `MODEL_UNAVAILABLE` | 503 | 是 | 等待模型服务恢复 |
|  |  | 50002 | `MODEL_TIMEOUT` | 504 | 是 | 按请求策略重试 |
|  |  | 50003 | `MODEL_RATE_LIMITED` | 429 | 是 | 按 `Retry-After` 退避 |
|  |  | 50004 | `MODEL_RESPONSE_INVALID` | 502 | 是 | 记录 Trace 后重试 |
| 60000-60999 | 评估 | 60001 | `EVALUATION_DATASET_INVALID` | 422 | 否 | 修正数据集 Schema |
|  |  | 60002 | `EVALUATION_RUN_NOT_FOUND` | 404 | 否 | 检查评估运行 ID |
| 90000-90999 | 基础设施 | 90001 | `DATABASE_UNAVAILABLE` | 503 | 是 | 稍后重试 |
|  |  | 90002 | `INTERNAL_ERROR` | 500 | 视情况 | 通过 `request_id` 联系维护者 |

没有证据是一个业务结果，不代表 HTTP 调用失败：接口返回 HTTP 200、`success=true`、`code=0`，在 `data.status` 中标记 `degraded`，并由回答策略返回明确拒答。只有没有可接受的结果且无法安全降级时，才返回非零错误码。

### 8.2 多层超时与请求 Deadline

超时采用从外到内传递的 Deadline。每个阶段拿到“剩余时间”，不得自行重新开始一个完整超时周期；并行的 Dense、Sparse 查询共享检索阶段预算。

开发期默认值如下，全部配置化：

| 层级/阶段 | 默认值 | 说明 |
| --- | ---: | --- |
| 客户端请求总超时 | 65 秒 | 前端应在服务端 Deadline 前主动取消 |
| API 总 Deadline | 60 秒 | 返回或流结束的硬上限 |
| 身份、所有权和 MySQL 查询 | 1000 毫秒 | 失败关闭，不使用未知权限 |
| Query 规范化与路由 | 500 毫秒 | 超时使用原 Query |
| Qwen Embedding 调用 | 5000 毫秒 | 命中 embedding 缓存时跳过 |
| OpenSearch 混合检索 | 3000 毫秒 | Dense 与 Sparse 并行，共享该预算 |
| Rerank | 3000 毫秒 | 超时回退到融合初排 |
| 上下文构造与压缩 | 1000 毫秒 | 超时使用受 Token 限制的原始证据 |
| Chat 首 Token | 10000 毫秒 | 超时进入模型降级或失败 |
| Chat 流式空闲 | 15000 毫秒 | 连续无数据超过此时间视为异常 |
| Chat 总生成 | 45000 毫秒 | 不得超过 API 总 Deadline |
| Trace、缓存和反馈写入 | 1000 毫秒 | 异步执行，不能阻塞答案返回 |

外部 HTTP 客户端还要分别设置 connect timeout、read timeout 和 total timeout。重试最多一次，且必须扣除已消耗的时间；鉴权、参数校验、业务无结果和已经开始输出 Token 的生成请求不重试。客户端断开连接时立即取消下游模型和检索任务。

### 8.3 熔断与降级状态

每个外部依赖按“依赖 + 操作”独立维护熔断器，例如 `opensearch.search`、`qwen.embedding`、`chat.completions`，避免一个故障拖累其他路径。熔断器使用 `CLOSED -> OPEN -> HALF_OPEN` 状态：

- `CLOSED`：正常请求，统计超时、连接错误和无效响应；
- `OPEN`：达到失败阈值后短路，直接走降级路径；
- `HALF_OPEN`：冷却后放行少量探测请求，成功则恢复，失败则重新打开。

开发期可采用“30 秒窗口内 5 次可计入失败，打开 15 秒，半开 2 个探测请求”的初始参数，进入压测后根据错误率和恢复时间调整。熔断状态、触发时间、恢复时间和探测结果写入阶段 Trace。

最终状态只允许三种：

- `completed`：主链路按计划完成；
- `degraded`：通过回退仍产生可用结果，答案携带 `degraded_reasons`；
- `failed`：没有可接受结果，返回统一错误体或 SSE `error` 事件。

首期固定降级原因：

| 原因 | 触发条件 | 回退路径 |
| --- | --- | --- |
| `QUERY_REWRITE_TIMEOUT` | Query 改写超时 | 使用原 Query |
| `EMBEDDING_TIMEOUT` | 千问 Embedding 超时 | 关键词检索；无结果则失败 |
| `DENSE_RETRIEVAL_UNAVAILABLE` | OpenSearch k-NN 分支失败 | 使用 BM25 分支 |
| `SPARSE_RETRIEVAL_UNAVAILABLE` | OpenSearch BM25 分支失败 | 使用 k-NN 分支 |
| `RETRIEVAL_TIMEOUT` | 混合检索超过预算 | 使用已完成的召回分支 |
| `RERANK_TIMEOUT` | Rerank 超时或熔断 | 使用 RRF 初排 |
| `CONTEXT_COMPRESSION_TIMEOUT` | 上下文压缩超时 | 使用未压缩证据并限制 Token |
| `LLM_TIMEOUT` | Chat 首 Token、空闲或总时长超时 | 返回检索摘要或暂时不可用提示 |
| `CACHE_UNAVAILABLE` | Redis 超时或熔断 | 跳过缓存继续主链路 |

阶段记录至少包含 `started_at`、`ended_at`、`duration_ms`、`timeout_ms`、`deadline_remaining_ms`、`status`、`retry_count`、`circuit_state`、`fallback` 和 `error_code`。根 Trace 记录总 Deadline、实际结束时间、超时阶段、熔断依赖、降级原因和最终状态。

对应 Trace JSON 中，`deadline_ms`、`deadline_started_at`、`deadline_ended_at` 描述请求总时间边界，`timeout_summary` 描述是否超时、超时阶段、是否超过总 Deadline 以及打开熔断的依赖；这些字段与 `docs/schemas/rag-query-trace.schema.json` 保持一致。

## 9. API 与模块边界

### 9.1 前后端接口

| 接口 | 作用 |
| --- | --- |
| `POST /api/v1/auth/login` | 用户登录并签发短期 JWT |
| `GET /api/v1/auth/me` | 获取当前登录用户 |
| `POST /api/v1/chat/completions` | 问答，支持 SSE 和非流式模式 |
| `GET /api/v1/conversations/{id}` | 对话与引用 |
| `POST /api/v1/messages/{id}/feedback` | 点赞、点踩和人工纠正 |
| `POST /api/v1/knowledge-bases` | 创建知识库 |
| `POST /api/v1/knowledge-bases/{id}/documents` | 上传文档，返回异步任务 ID |
| `GET /api/v1/ingestion-jobs/{id}` | 查询入库状态和失败原因 |
| `GET /api/v1/knowledge-bases/{id}/versions` | 查看和切换已发布版本 |
| `POST /api/v1/evaluation-runs` | 发起离线评估 |
| `GET /api/v1/health` | 存活检查 |
| `GET /api/v1/ready` | 就绪检查，区分必要和可选依赖 |

问答响应至少包含 `request_id`、`conversation_id`、`answer`、`citations`、`status`、`knowledge_base_version`、`model_version` 和 `degraded_reasons`。SSE 事件使用 `start`、`delta`、`citation`、`debug`、`heartbeat`、`end` 和 `error`。

### 9.2 SSE 事件协议

问答流使用标准 `text/event-stream`。客户端发起 `POST /api/v1/chat/completions`，通过 `Accept: text/event-stream` 请求流式响应；服务端每个事件以 `event:` 指定事件名，以一个或多个 `data:` 行传输 JSON，事件之间使用空行（两个换行符 `\n\n`）分隔。

每个事件都使用统一外壳：

```json
{
  "type": "delta",
  "seq": 2,
  "request_id": "req_01J...",
  "trace_id": "trace_01J...",
  "terminal": false,
  "data": {}
}
```

约束：

- `seq` 从 1 开始单调递增，客户端可以检测丢失或乱序；`id` 可使用相同的序号，便于调试。
- `request_id` 用于业务关联，`trace_id` 用于链路关联；首期可以让两者使用同一个 ULID，但字段保留以便以后接入分布式 Trace。
- 一个流只能有一个终止事件：`end` 或 `error` 二选一，发送后立即关闭连接。
- 流建立前发生的错误使用普通 HTTP 错误响应；HTTP 头发送后发生的错误必须使用 SSE `error` 事件，因为此时不能再修改 HTTP 状态码。
- `debug` 只在开发环境且请求明确开启时发送，生产环境禁止发送原始 Query、Prompt、文档正文和敏感 ID。
- 服务端每 10-15 秒发送一次 `heartbeat`，防止代理误判连接空闲；心跳不增加答案文本。

事件定义：

| 事件 | `terminal` | `data` 内容 | 客户端处理 |
| --- | --- | --- | --- |
| `start` | `false` | `trace_id`、知识库版本、模型版本和开始时间 | 初始化消息和 Trace |
| `delta` | `false` | `text` 文本增量、内容序号 | 追加文本，形成打字机效果 |
| `citation` | `false` | `chunk_id`、文档 ID、标题和引用位置 | 更新引用面板 |
| `debug` | `false` | 阶段、耗时、召回 ID 等开发信息 | 仅开发面板展示 |
| `heartbeat` | `false` | 服务端时间 | 保持连接，不更新答案 |
| `end` | `true` | 最终状态、结束原因、引用校验、Token 和成本 | 保存完整答案并关闭流 |
| `error` | `true` | 错误码、错误类型、消息、是否可重试和是否存在部分答案 | 展示错误并按策略重试 |

示例：

```text
event: start
id: 1
data: {"type":"start","seq":1,"request_id":"req_01J...","trace_id":"trace_01J...","terminal":false,"data":{"status":"started","knowledge_base_version":"12"}}

event: delta
id: 2
data: {"type":"delta","seq":2,"request_id":"req_01J...","trace_id":"trace_01J...","terminal":false,"data":{"text":"通常会在","content_index":0}}

event: citation
id: 3
data: {"type":"citation","seq":3,"request_id":"req_01J...","trace_id":"trace_01J...","terminal":false,"data":{"chunk_id":"chunk_001","document_id":"doc_001","title":"退款制度"}}

event: end
id: 4
data: {"type":"end","seq":4,"request_id":"req_01J...","trace_id":"trace_01J...","terminal":true,"data":{"status":"completed","finish_reason":"stop","usage":{"input_tokens":120,"output_tokens":35},"degraded_reasons":[]}}
```

`EventSource` 适合 GET、Cookie 鉴权的专用流接口；当前聊天接口是 POST 并通常需要 Authorization Header，前端推荐使用 `fetch` 读取 `ReadableStream`，解析 SSE 帧后实现打字机效果。无论使用哪种客户端，解析器都应处理多行 `data:`、心跳、网络重连和终止事件。

### 9.3 后端目录

```text
app/
  api/                 # 路由、鉴权依赖、请求响应模型
  core/                # 配置、日志、异常、数据库和 Redis
  domain/              # 领域实体和端口接口，不依赖基础设施
  services/
    chat/              # 对话编排、Query 理解、模型路由
    retrieval/         # 混合检索、融合、Rerank、上下文
    knowledge/         # 文档、版本、发布和所有权
    ingestion/         # 解析、切分、Embedding、索引任务
    evaluation/        # 数据集、指标和回放
  infrastructure/
    mysql/             # SQLAlchemy models/repositories/migrations
    redis/             # cache、lock、rate limit、hot query
    retrieval/         # OpenSearch adapter
    models/            # Embedding/Rerank/LLM adapters
    storage/           # 本地文件存储 adapter，预留对象存储实现
  workers/             # 异步任务入口，与 API 生命周期隔离
tests/
```

SQLAlchemy Session 不跨请求或任务复用。配置通过环境变量注入，尤其是 `DATABASE_URL`、`REDIS_URL`、OpenSearch 地址、索引名称和 OpenAI-compatible API 配置。密码、Token 和 API Key 禁止写入代码、文档或镜像。

## 10. 安全与治理

- 首期使用本地账号和 JWT；后续可接入企业 IAM/OIDC，认证适配器替换不影响资源所有权模型。
- 管理操作、文档访问、回答生成和权限失败都写审计日志。
- 日志默认脱敏，不记录完整 Query、Prompt、答案或正文；敏感内容需受控采样和加密。
- 上传文档检查类型、大小、压缩炸弹、恶意内容和 Prompt Injection。
- 输出执行敏感信息过滤、引用校验和高风险人工复核策略。
- 使用最小权限数据库账号、Redis ACL、本地文件目录权限和检索引擎访问策略。
- 个人资源隔离同时由应用查询约束、索引过滤和自动化测试验证。

## 11. 可观测性与评估衔接

每次请求使用同一个 `request_id`，Trace 至少包含：

- Query 规范化结果和意图类型（敏感字段脱敏）；
- 用户、角色、知识库、Prompt、Chat/Embedding/Rerank 模型、Embedding 维度、索引和检索策略版本；
- 各阶段耗时、超时、重试、降级原因和缓存命中；
- `embedding_model`、`embedding_dimension`、`chunk_strategy_version`、`index_version`、`retrieval_version`、`dense_k`、`sparse_k`、`final_top_k`、`fusion_strategy`；
- `prompt_version`、`chat_model`、`timeout`、`degraded_reason`、检索 Chunk ID、`context_snapshot` 和 `citations`；
- 输入输出 Token、成本、首 Token 时间和总耗时；
- 引用校验、用户反馈和后续追问。

### 11.1 评估闭环

```text
线上 Trace / 用户反馈
  -> 脱敏、采样和人工标注
  -> 评估数据集版本
  -> 检索：Hit@K、Recall@K、MRR、nDCG
  -> 上下文：Relevance、Precision、Recall、重复和冲突
  -> 生成：Faithfulness、Relevance、Correctness、Completeness
  -> 引用正确性、拒答质量、延迟、Token 和成本
  -> 诊断矩阵
  -> 调整切分、检索、Rerank、Prompt 或模型
```

评估运行必须记录完整配置，不能只保存最终分数。RAGAS、DeepEval 或 ARES 可以作为评估器，但需要抽样人工校准 LLM-as-judge；发布新索引、Prompt 或模型前执行固定回归集。

### 11.2 为评估保留的扩展点

当前架构已经具备接入 RAG 评估的基础，但实现时必须把以下对象设计成稳定接口，而不是把逻辑写死在 Chat 服务中：

| 扩展点 | 建议接口/记录 | 评估用途 |
| --- | --- | --- |
| 检索器 | `Retriever.retrieve(query, filters, top_k)` | 对比 Dense、Sparse、Hybrid 和不同 Top-K |
| 融合器 | `FusionStrategy.merge(candidates)` | 对比 RRF、加权融合和业务规则 |
| Reranker | `Reranker.rank(query, candidates)` | 对比无 Rerank、轻量和高质量模型 |
| 上下文构造 | `ContextBuilder.build(query, ranked_chunks, budget)` | 评估去重、压缩、截断和 Token 预算 |
| 生成器 | `Generator.generate(context, prompt_version, model)` | 对比 Prompt、模型和拒答策略 |
| 评估器 | `Evaluator.evaluate(case, trace)` | 接入规则指标、RAGAS、DeepEval 和人工评分 |
| 数据集 | `DatasetSnapshot` | 固定问题、标准答案、gold Chunk、问题类型和知识库版本 |

每次离线评估运行保存一份不可变的 `run_manifest`，至少包括：

- 评估数据集版本和知识库版本；
- Chunk 策略、解析器版本、Embedding 模型/维度和 OpenSearch 索引版本；
- Query Rewrite、检索通道、融合策略、`top_k`、Rerank、上下文预算和 Prompt 版本；
- Chat 模型、模型参数、API 供应商标识、评估器版本和运行时间；
- 每个 Case 的召回 Chunk、最终上下文、回答、引用、耗时、Token、成本和错误/降级状态。

这样才能回答“效果变好是因为 Embedding、切分、检索、Prompt 还是模型变化”，也能对线上 Trace 做离线回放。评估服务只依赖 Trace 和 Dataset Snapshot，不直接依赖 Redis 缓存；回放默认关闭答案缓存，避免缓存命中掩盖真实链路效果。

### 11.3 当前架构成熟度判断

对于当前的测试规模，架构已经足够进入工程实现：技术栈、检索边界、权限模型、缓存、降级、版本和评估入口均已明确。它属于“可扩展的模块化单体基线”，还不是生产级平台。

以下两项已经落地为开发期契约：

1. OpenSearch mapping、开发默认 `dense_k=5`、`sparse_k=5`、`final_top_k=5`、默认 BM25，以及应用层关键词/向量并行查询协议。
2. `run_manifest`、Dataset Snapshot、线上 Trace 的 MySQL 表设计及 JSON Schema。

第 4 项“最小回归集”在优化链路搭建完成后，与 RAG 评估一并创建。协议实现仍允许根据实际前端联调和压测结果做兼容性扩展，但新增错误码、事件类型和状态值必须遵循本节的版本化规则。

完成后续两项后，增加 RAGAS/DeepEval、替换 Embedding 模型、调整 Chunk 策略或增加 Rerank 都不需要重写在线问答主链路。

## 12. 部署与配置

首期部署单元：

```text
frontend              # 独立构建和部署
rag-api               # FastAPI，无状态，可水平扩展
rag-worker            # 解析、Embedding、索引和评估任务
mysql                 # 事务与元数据
redis                 # 缓存、锁、限流和短期状态
opensearch            # k-NN Dense + BM25 Sparse 统一检索索引
local-file-storage    # 测试期原始文档和解析文件
openai-compatible-api # Chat、Embedding 和可选 Rerank API
```

API 实例保持无状态，会话和任务状态放入 MySQL/Redis。数据库连接池、Redis 连接池、HTTP 客户端和模型并发数都要设置上限。API 与 Worker 使用相同代码和配置版本，但独立扩缩容。

环境变量示例仅展示变量名，仓库中的 `.env.example` 应使用占位值：

```env
APP_ENV=dev
DATABASE_URL=mysql+pymysql://user:password@mysql:3306/rag
REDIS_URL=redis://redis:6379/0
LOCAL_FILE_ROOT=./data/files
OPENSEARCH_URL=http://opensearch:9200
OPENAI_BASE_URL=https://api.example.invalid/v1
OPENAI_API_KEY=replace-me
CHAT_MODEL=chat-model
QWEN_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_EMBEDDING_API_KEY=replace-me
QWEN_EMBEDDING_MODEL=qwen-embedding-model
RERANK_MODEL=rerank-model
```

生产环境使用密钥管理服务或平台 Secret 注入敏感变量，`.env` 不提交仓库。

## 13. 实施顺序

### Phase 1：可用基线

- FastAPI、SQLAlchemy、MySQL、Redis、`uv` 工程骨架；
- 用户、角色、知识库、文档和版本数据模型；
- 本地上传、解析、固定规则 Chunk、Embedding 和 OpenSearch 统一索引；
- Dense + BM25、基于 `owner_id` 的权限过滤、SSE 问答和引用；
- Embedding、检索结果和稳定答案缓存；
- 结构化日志、Trace、健康检查和错误码。

### Phase 2：质量与可靠性

- Query Rewrite、意图路由、两阶段 Rerank 和上下文压缩；
- 超时、熔断、降级、singleflight、限流和 Worker 重试；
- 文档版本原子发布、索引校验和回滚；
- 离线评估数据集、指标、回放和人工标注闭环。

### Phase 3：规模化

- 指标驱动的热点缓存预热和逻辑过期；
- 多模型路由、本地 GPU 推理、batch、量化和推理扩展；
- 索引分片、副本、冷热分层和更多召回通道；
- 成本预算、用户配额、质量门禁和自动回归。

## 14. 后续扩展决策

1. 文档来源：继续本地上传，还是增加飞书、Confluence、SharePoint、数据库或对象存储 Connector。
2. 身份权限：何时从本地 JWT 升级为企业 OIDC/IAM，以及是否需要部门和文档级 ACL。
3. 模型服务：OpenAI-compatible API 是否需要多供应商路由、重试、配额和成本统计。
4. OpenSearch 演进：多实例部署前如何配置分片、副本、k-NN 索引和资源容量。
5. 实时性目标：知识库发布后允许多久生效，这决定索引发布和缓存策略。
6. 规模目标：文档数、Chunk 数、并发、P95 延迟和请求成本预算。

当前测试阶段按“OpenSearch + MySQL + Redis + 本地文件存储 + OpenAI-compatible API”作为默认基线。OpenSearch 同时承担向量和全文检索，覆盖当前优化文档中的混合检索、个人/管理员权限、分层缓存、降级和评估闭环。
