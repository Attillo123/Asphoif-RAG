优化RAG全链路耗时
Embedding（千问）向量化->应用层并行检索（OpenSearch k-NN/BM25）->RRF融合->排序rerank->LLM生成

embedding层优化：
1.本地部署+GPU推理加速
2.TensorRT/vLLM/ONNX
3.cache query embedding,缓存一些高频向量,缓存需要考虑规范化,应该归一化后再判断是否命中，不能只做简单字符串缓存
4.增量索引和离线预计算,新文档只生成新增 chunk 的向量，避免全量重算


检索层优化：
1.vector DB调优,HNSW(近似最近邻搜索算法),ef(调整参数，牺牲精度换取召回的速度),预加载(内存加载)
2.元数据过滤(按照部门/级别/时间等进行划分,入库的时候给向量打上标签)
3.召回数量
检索层采用应用层并行的混合检索：
除了向量检索，企业级 RAG 通常还需要：
- Dense retrieval：语义相似度
- Sparse retrieval：BM25、倒排索引、关键词
- Hybrid retrieval：向量召回和关键词召回并行，再进行融合
- 多路召回：标题、正文、标签、实体、FAQ 等不同字段分别召回
链路：
Query
  ├─ Dense retrieval
  ├─ BM25 / keyword retrieval
  ├─ Metadata filtering
  └─ Business rule retrieval
        ↓
      应用层 RRF Fusion

首期不使用 OpenSearch Hybrid DSL。Dense 和 Sparse 使用同一 OpenSearch 索引并行查询，分别记录 `dense_k`、`sparse_k`，融合后使用 `final_top_k`，以便后续评估质量和延迟。


排序层优化(排序时延占比是比较高的)：
1.小模型排序(如果不理想可以使用领域数据微调)
2.batch(批量)+异步
3.只对高价值、低置信度或复杂问题使用 rerank,可以使用轻量 reranker 做第一层筛选，再用高质量 reranker 处理少量候选,文档数量较多时，不要把所有召回结果都送进 reranker,rerank 前先去重、合并相邻 chunk，可以减少计算量

LLM生成阶段优化:
1.上下文压缩
去掉重复 chunk
保留与问题最相关的句子
对长文档做摘要
压缩历史对话
只保留证据片段而不是完整文档
2.推理加速
3.streaming流失返回
4.cache高频问题缓存,高频问题直接做成key-value,从缓存库中取
5.动态模型路由,简单问题使用小模型，复杂问题使用大模型
6.模型量化和推理参数调优
- INT8、INT4、FP8
- 合理设置 max_tokens
- 限制不必要的思考长度
- Prefix cache / KV cache
- Continuous batching
- Speculative decoding

架构层优化
1.向量库分片/分布式(水平扩容),提高吞吐量
2.多级缓存(全链路cache),query->召回->问答,都可以缓存
3.高频hot data放内存
企业级系统还需要考虑异常路径：
- embedding、BM25、向量检索并行执行
- 检索和权限校验尽量并行
- 设置各阶段 timeout
- rerank 超时则退化为向量初排
- 向量库异常则退化到关键词检索或 FAQ
- LLM 超时则返回检索结果、摘要或明确的暂时不可用提示
- 对不同阶段设置独立线程池和限流，避免某一环节拖垮全链路
4.缓存需要校验版本
问答缓存不能只以 query 为 key，还应考虑：
- 用户权限
- 租户
- 知识库版本
- 文档更新时间
- Prompt 版本
- 模型版本
否则可能返回过期内容或越权内容

企业级RAG架构图:
用户问题进入->缓存命中?->embedding向量化->元数据过滤->向量检索->轻量级Reranker->送出top-k给大模型->流式返回

一个更完整的线上链路:
请求进入
  ↓
身份认证 / 权限确定
  ↓
Query 规范化与意图识别
  ↓
缓存检查
  ├─ 命中 → 返回缓存结果
  └─ 未命中
        ↓
      路由判断
        ├─ FAQ / 简单问题 → 关键词或结构化查询
        └─ 复杂问题
              ↓
        Dense + Sparse + Metadata 并行召回
              ↓
        结果融合、去重、过滤
              ↓
        条件式 Rerank
              ↓
        上下文压缩
              ↓
        LLM 生成
              ↓
        引用校验 / 安全检查
              ↓
        流式返回并写入缓存


一个更准确的总结

Query 理解
→ 缓存检查
→ 权限与元数据过滤
→ Dense / Sparse 混合召回
→ 结果融合和去重
→ 条件式 Rerank
→ 上下文压缩
→ 模型路由与 LLM 生成
→ 引用校验和安全检查
→ 流式返回
→ 分层缓存与反馈闭环

最值得补充的优化点是：
1. Dense + Sparse 混合检索
2. Query 改写和意图路由
3. 条件式 rerank，而不是全量 rerank
4. 上下文压缩和动态模型路由
5. 权限过滤和安全校验
6. 分阶段 timeout、降级和熔断
7. 统一的离线评测与线上观测体系
