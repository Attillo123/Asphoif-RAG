传统指标:
blue/rouge只比较字面重叠
完全无视检索上下文
无法检测幻觉-RAG最核心的风险
(但学术研究中仍是最常用的)


Faithfulness-忠实度
Answer Relevance-答案相关性
Context Relevance-上下文相关性

工具:
RAGAS
DeepEval
ARES

RAG做效果评估:
检索:
MRR平均倒排率
前k项hits rate命中率
NDCG排序指标

生成:
量化指标rouge-l,文本相似度
多样性
引入人工评估




推荐流程：

1. 明确评估目标和测试集
测试集至少包含：
{
  "question": "退款多久到账？",
  "reference_answer": "通常会在3-5个工作日内到账。",
  "gold_chunk_ids": ["chunk_102", "chunk_108"],
  "question_type": "事实问答",
  "is_unanswerable": false
}
建议额外标注：
- 问题类型：事实、总结、多跳、比较、条件判断等；
- 是否允许多个答案；
- 是否需要引用；
- 不可回答问题；
- 证据相关等级：0、1、2；
- 数据版本和知识库版本。

使用ai生成测试文档和高质量测试集

2. 检索侧评估
自己基于标准证据 ID 计算：
Recall@K
Precision@K
MRR
nDCG@K
Hit@K

评估必须消费线上 Trace 或等价的回放 Trace，并固定记录：
- embedding_model、embedding_dimension；
- chunk_strategy_version、index_version、retrieval_version；
- dense_k、sparse_k、final_top_k、fusion_strategy；
- prompt_version、chat_model、timeout、degraded_reason；
- retrieved_chunk_ids、context_snapshot、citations。

同时可以记录：
- 向量检索 Recall；
- 混合检索 Recall；
- Reranker 后 Recall；
- 不同 Top-K 下的结果；
- 平均检索延迟和 P95 延迟。

评估运行默认关闭答案缓存，避免缓存命中掩盖真实链路效果。每个 Case 通过 `request_id` 关联 `query_trace`，并将完整配置写入 `run_manifest`，从而比较不同 Embedding、Chunk、索引、K 值、融合策略、Prompt 和 Chat 模型。

3. 上下文质量评估
Context Relevance 和 Context Precision 更准确地说属于检索结果或上下文构造质量，不完全属于生成侧。
可以使用 RAGAS/DeepEval 评估：
- Context Relevance；
- Context Precision；
- Context Recall；
- 上下文是否包含重复内容；
- 上下文是否存在冲突；
- 上下文长度是否过长；
- 证据是否被截断。

4. 生成侧评估
除了 Faithfulness 和 Answer Relevance，建议至少增加：
Answer Correctness
答案事实是否正确，是否符合标准答案。
Completeness
答案是否覆盖了标准答案中的关键点。
Citation Correctness
引用的文档或 Chunk 是否真的支持对应陈述。
Citation Completeness
重要陈述是否都提供了引用。
Abstention Quality
知识库没有答案时，系统是否能够正确拒答，而不是编造答案。

5. 构建诊断矩阵
你的诊断矩阵可以进一步细化为：
检索	答案	结论
命中证据	正确	正常
命中证据	错误	生成、Prompt 或模型理解问题
未命中证据	正确	可能依赖模型先验知识，存在风险
未命中证据	错误	优先排查检索问题
没有证据	肯定回答	幻觉或拒答能力不足
有证据	拒答	模型没有正确利用上下文
有正确证据	引用错误	引用绑定或生成问题
有多个证据	答案不完整	多文档融合问题

建议按问题类型切片统计

6. 人工评估和 LLM-as-judge 校准
RAGAS/DeepEval 本质上通常还是 LLM-as-judge，因此建议抽取一部分样本进行人工复核。

7. 增加工程指标
离线质量之外，还应记录：
端到端:P50/P95 延迟
单次请求 Token 数
单次请求成本
检索失败率
模型调用失败率
上下文长度
平均返回 Chunk 数
用户追问率
用户点赞/点踩
