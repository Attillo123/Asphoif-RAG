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

## 8. 评估方案和评估器分工

本项目采用“确定性指标 + RAGAS/LLM-as-judge + 人工评估”的组合，而不是把 RAGAS 当作唯一评分来源。RAGAS 负责批量评估上下文和答案质量；检索排序指标根据 gold chunk ID 直接计算；引用正确性、拒答质量等企业规则通过自定义评估器补充；抽样样本由人工复核，用于校准 LLM-as-judge 并处理高风险结论。

| 评估器 | 负责内容 | 结果来源 |
| --- | --- | --- |
| RetrievalMetricsEvaluator | Recall@K、Precision@K、MRR、nDCG@K、Hit@K | gold_chunk_ids 与 retrieved_chunk_ids 的代码计算 |
| RagasEvaluator | Context Relevance、Context Precision/Recall、Faithfulness、Answer Relevance、Answer Correctness | RAGAS、固定 judge 模型和评分提示词 |
| CitationEvaluator | Citation Correctness、Citation Completeness、证据覆盖 | 引用与 Chunk 的规则校验，必要时由 LLM 辅助 |
| AbstentionEvaluator | 无答案时是否拒答、是否出现无证据肯定回答 | is_unanswerable、答案和上下文的规则/LLM 判断 |
| HumanReviewEvaluator | 高风险、低置信度和抽样 Case 的人工评分 | 人工标注及校准记录 |

RAGAS、DeepEval 和 ARES 均通过 `Evaluator` 适配器接入。运行清单必须记录工具名称、版本、judge 模型、评分提示词版本和失败原因。人工评分作为独立字段保存，并记录评审人、时间和标注规范版本。

## 9. 评估运行和平台能力

一次 `Evaluation Run` 固定一个不可变 Dataset Snapshot，并按 Case 读取线上 Trace 或执行关闭答案缓存的回放。不同评估器的结果分别保存，不把来源不同的分数混成无法解释的总分。

阶段 8 的系统应支持：

1. 创建、发布和管理不可变 Dataset Snapshot；
2. 管理 Case 及其标准答案、gold chunk、问题类型和不可回答标记；
3. 创建、执行、取消和查询 Evaluation Run；
4. 将线上 Trace 与 Case 绑定，或执行关闭答案缓存的回放；
5. 展示自动指标、RAGAS 指标、人工评分和诊断矩阵；
6. 比较不同 run_manifest 的模型、Prompt、Embedding、Chunk、Top-K 和融合策略；
7. 导出 Case 结果和回归对比报告。

每个 Case 至少保留 `question`、`reference_answer`、`gold_chunk_ids`、`question_type`、`is_unanswerable`、`requires_citation`、知识库版本和数据集版本。评估结果至少保留检索结果、上下文快照、答案、引用、各评估器分数、诊断结论、延迟、Token、成本和错误信息。

## 人工评分固定量表 human-0-5-v1

控制台逐 Case 使用六项固定整数评分：答案正确性 `answer_correctness`、答案完整性 `answer_completeness`、忠实度 `faithfulness`、引用正确性 `citation_correctness`、引用完整性 `citation_completeness`、总体评分 `overall`。总体评分由评审人综合判断，不自动取平均。

- 0：完全不满足；1：严重问题；2：较多问题；3：基本满足，仍需修改；4：良好，仅有轻微问题；5：完全满足。
- 正确性对照标准答案及业务规则；完整性检查关键要点和条件；忠实度检查事实是否有上下文支持；引用正确性检查证据是否支持对应结论；引用完整性检查关键结论是否有引用。
- 无需引用且没有待支撑结论时，两项引用评分记 5；错误引用或无依据结论仍须扣分并说明。不可回答问题结合拒答合理性评审。
- 默认未评分，不能将空值转成 0；提交时六项必须齐全，备注选填且不超过 2000 字。
- 结果保存在 `metrics.human_review`，记录评审人、时间、量表版本；自动评分保持独立。再次保存替换上次人工评分，目前不是多评审人历史记录。
- 新量表由服务端校验字段及范围，旧 `v1` 记录保持可读取；旧单项评分回显后需补齐其余项目才能提交新量表。
