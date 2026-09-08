可以先按“缓存什么、缓存多久、什么情况下失效、如何避免缓存击穿”来设计。对于 RAG，建议不要只做一个总缓存，而是按链路拆成多级缓存。

一、推荐的 Redis 缓存层级
用户问题
  ↓
L1：最终答案缓存
  ↓ 未命中
L2：Query embedding 缓存
  ↓
L3：检索结果缓存
  ↓
L4：Rerank 结果缓存
  ↓
LLM 生成
  ↓
写入对应缓存
最先落地时，可以只实现：
1. Query embedding 缓存
2. 检索结果缓存
3. 最终答案缓存
Rerank 缓存可以后续再加，因为它依赖候选文档和模型版本，失效条件比较复杂。

二、各类缓存如何设计
1. Query embedding 缓存
适合缓存高频、重复或近似重复的问题。
Key 示例：
rag:emb:v1:{embedding_model}:{normalized_query_hash}
Value：
{
  "model": "qwen-embedding-model",
  "provider": "qwen",
  "dimension": "QWEN_EMBEDDING_DIMENSION",
  "vector": [...]
}
建议：
- Query 先做规范化：
  - 去除首尾空格
  - 统一大小写
  - 统一全角半角
  - 处理多余标点
  - 可选：同义表达归一化
- TTL 可以设置为 1~7 天
- Embedding 模型升级时，通过模型版本自然隔离
- 不建议一开始就缓存所有 query，优先缓存高频 query
- 向量较大时需要关注 Redis 内存，必要时使用压缩或只缓存高频问题
注意：embedding 是浮点数组，单条可能占用数 KB。高 QPS 场景下，缓存全部历史 query 会快速膨胀。
2. 检索结果缓存
一般比 embedding 缓存更有价值，因为可以直接跳过 embedding 和向量库查询。
Key 示例：
rag:retrieve:v1:{owner_id}:{knowledge_base_version}:{query_hash}:{filter_hash}:{retrieval_version}:{dense_k}:{sparse_k}:{final_top_k}
Value 可以存：
{
  "items": [
    {
      "doc_id": "doc_123",
      "chunk_id": "chunk_456",
      "score": 0.87,
      "title": "报销制度",
      "content": "...",
      "metadata": {
        "department": "finance"
      }
    }
  ],
  "retriever_version": "hybrid-v3",
  "created_at": "2026-09-07T10:00:00"
}
Key 中至少要包含：
- 租户或用户隔离信息
- 知识库 ID
- 知识库版本
- Query
- metadata filter
- 检索策略版本
- topK
- embedding 模型或索引版本
例如下面这些请求不能共用缓存：
同一个问题 + 不同部门权限
同一个问题 + 不同知识库
同一个问题 + 不同时间过滤条件
同一个问题 + 不同 topK
TTL 建议：
- 知识库更新频繁：5~30 分钟
- 制度、文档更新较少：1~24 小时
- 通过知识库版本控制时，可以使用较长 TTL，更新时直接切换版本
3. Rerank 结果缓存
Key 示例：
rag:rerank:v1:{rerank_model}:{candidate_ids_hash}:{query_hash}
Value：
{
  "ordered_ids": [
    "chunk_456",
    "chunk_123",
    "chunk_789"
  ],
  "scores": [0.95, 0.82, 0.71]
}
需要注意：
- 候选文档顺序是否影响结果
- 候选文档内容是否变化
- rerank 模型版本是否变化
- 是否包含用户权限和过滤条件
因此建议对 candidate IDs 排序后再计算 hash，避免同一批候选因为顺序不同产生不同 key。
TTL 通常设置为：
30 分钟 ~ 6 小时
如果 rerank 模型或文档内容经常变化，则设置更短。
4. 最终答案缓存
Key 示例：
rag:answer:v1:{owner_id}:{knowledge_base_version}:{prompt_version}:{chat_model}:{embedding_model}:{retrieval_version}:{query_hash}
Value 建议保存：
{
  "answer": "......",
  "citations": [
    {
      "doc_id": "doc_123",
      "chunk_id": "chunk_456",
      "title": "报销制度"
    }
  ],
  "model": "your-llm",
  "prompt_version": "prompt-v5",
  "created_at": "2026-09-07T10:00:00"
}
不要只缓存纯文本答案，最好同时保存：
- 引用来源
- 使用的文档版本
- 模型版本
- Prompt 版本
- 生成时间
- 是否经过审核
TTL 建议按照内容类型区分：
内容类型	建议 TTL
静态 FAQ	1~7 天
公司制度	1~24 小时
实时业务状态	10 秒~5 分钟
用户个性化答案	1~30 分钟
高风险领域答案	关闭或极短 TTL


最重要的一点是：最终答案缓存必须考虑权限。不能因为 query 相同，就让不同用户共享答案。

三、缓存 Key 的通用结构
建议统一采用：
rag:{environment}:{layer}:{version}:{scope}:{hash}
例如：
rag:prod:answer:v1:user_001:abc123
rag:prod:retrieve:v2:user_001:def456
rag:prod:emb:v1:global:ghi789
其中：
- environment：dev、test、prod
- layer：emb、retrieve、rerank、answer
- version：缓存格式或业务版本
- scope：owner、角色范围、知识库范围
- hash：规范化参数后的 SHA-256 或短 hash
不要把完整 query、完整过滤条件直接放进 Key，容易导致 Key 过长，也可能泄露敏感信息。

四、缓存失效策略
缓存设计的核心不是 TTL，而是失效。
方案一：TTL 失效
最简单：
SET key value EX 3600
适合：
- FAQ
- 不经常变化的知识库
- 对实时性要求不高的场景
缺点是知识库更新后，旧答案仍可能在 TTL 内返回。
方案二：知识库版本失效
给每个知识库维护一个版本号：
rag:kb_version:{owner_id}:{kb_id} -> 2026090710
缓存 Key 中带上版本：
rag:retrieve:v1:user_001:kb_2026090710:query_hash
文档发生新增、删除、修改时，更新知识库版本。新请求自然使用新 Key，旧缓存无需逐条删除。
这是比较推荐的方式，因为比扫描 Redis 删除大量 Key 更安全。
方案三：按文档反向关联失效
如果某个文档更新，需要删除所有引用它的缓存，可以维护集合：
rag:doc_cache_refs:{doc_id}
集合中保存相关缓存 Key。
但这种方案维护成本较高，缓存量大时也可能产生大量关联关系。通常建议优先使用知识库版本控制。

五、必须考虑缓存穿透、击穿和雪崩
1. 缓存穿透
用户不断请求不存在的问题，缓存里也没有，每次都会打到向量库和 LLM。
处理方式：
- 对“无结果”设置短 TTL，例如 30 秒~5 分钟
- 对明显非法或过长 query 直接拦截
- 对 query 做规范化和限流
例如：
rag:answer:negative:{query_hash} -> NO_RESULT
2. 缓存击穿
某个热点 Key 过期时，大量请求同时重建缓存。
处理方式：
- 分布式锁
- Singleflight
- 热点 Key 逻辑不过期
- 提前异步刷新
推荐逻辑：
缓存命中且未过期 → 直接返回
缓存逻辑过期但仍可用 → 返回旧值，后台刷新
缓存完全不存在 → 只有一个请求负责重建
3. 缓存雪崩
大量 Key 在同一时间过期。
处理方式：
- TTL 增加随机抖动
例如：
实际 TTL = 基础 TTL + random(0, 300)
不要让所有缓存都统一在整点过期。

六、权限隔离和缓存边界
最终答案缓存和检索结果缓存不能只用 query 做 Key。
首期权限模型较简单，缓存 Key 至少要考虑：
owner_id
role（personal/admin）
knowledge_base_id
permission_version
query
filter
model_version
prompt_version
knowledge_base_version
首期直接将 `owner_id` 和 `role` 纳入 Key，保证个人数据不会因为缓存复用而越权；管理员代查时，`owner_id` 必须是实际被访问资源的所有者，而不是管理员自身。权限或知识库内容发生变化时递增 `permission_version` 或 `knowledge_base_version`，使旧缓存自然失效。
后续如果引入组织、租户或权限组，再把权限集合规范化为独立的 `permission_scope_hash`，并将它替换当前的用户维度；在此之前不要提前引入多租户语义，避免缓存边界与授权模型不一致。

七、Redis 数据结构怎么选
String
最适合：
- JSON 检索结果
- JSON 最终答案
- embedding 序列化结果
这是最常用的选择。
Hash
适合保存缓存元数据，例如：
answer
model_version
created_at
kb_version
但如果整体读取，String + JSON 通常更简单。
Set
适合：
- 文档和缓存 Key 的关联
- 热点 query 集合
- 需要去重的数据
Sorted Set
适合：
- 统计 query 热度
- 记录访问次数
- 做热点问题 TopN
- 维护缓存刷新优先级
例如可以维护：
rag:hot_queries:{owner_id}
score 使用访问次数或最近访问时间。

八、缓存命中策略建议
第一阶段：Cache Aside
推荐先用 Cache Aside：
1. 查询 Redis
2. 命中则返回
3. 未命中则执行 RAG
4. 写入 Redis
5. 返回结果
优点是简单、容易排查，也不会让 Redis 成为强依赖。
不要一开始就使用复杂的 Redis Stream、消息队列或主动缓存预热，除非已经明确有热点数据和高并发需求。

九、建议的初始配置
可以先从下面这套策略开始：
缓存层	是否建议首期启用	TTL
Query embedding	是	1~3 天
检索结果	是	10 分钟~1 小时
Rerank 结果	可选	30 分钟
最终答案	是，但只针对稳定问题	10 分钟~24 小时
无结果缓存	是	1~5 分钟


初期重点观察：
- 各层命中率
- Redis 内存使用
- 平均 Key 大小
- P95 延迟
- 缓存命中后的实际耗时
- 知识库更新后的旧数据比例
- 缓存答案的权限正确性

十、推荐的整体策略
比较稳妥的设计是：
公共 FAQ / 稳定知识
    → 可以缓存最终答案

动态知识 / 权限敏感知识
    → 只缓存 embedding 和检索结果

实时数据
    → 不缓存最终答案，或只使用极短 TTL

知识库更新
    → 更新知识库版本号，不逐条扫描删除缓存

热点问题
    → 使用热点统计和后台预热

缓存未命中
    → 使用分布式锁或 singleflight，避免重复生成
最初不要追求“所有阶段都缓存”。建议先从“检索结果缓存 + 稳定问题答案缓存”开始，这两层通常能获得比较明显的收益，同时不会让缓存失效和权限问题过于复杂。
