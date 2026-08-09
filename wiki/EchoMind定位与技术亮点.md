# EchoMind 定位与技术亮点

这份文档面向刚接触 EchoMind 的朋友，用来快速理解：这个项目是什么、解决什么问题、为什么不只是一个普通聊天 Demo，以及它有哪些值得关注的工程设计。

## 一句话定位

EchoMind 是一个面向复杂客服任务的多 Agent 客服编排运行时。

它不是单个“客服机器人”，而是一个可以承载多个专业 Agent 的协同系统：系统会先理解用户问题，再结合知识库、记忆、规则和运行状态，决定由哪个 Agent 主处理、是否需要其他 Agent 辅助，并持续监控和评测整体质量。

## 它解决什么问题

真实客服场景里的问题往往不是简单问答。

用户可能同时提到：

- 订单、物流、会员、积分等通用咨询
- 登录失败、401/500、页面崩溃等技术问题
- 退款、发票、重复扣款、支付失败等账务问题
- 转人工、投诉、紧急处理等升级诉求

如果只用一个 Agent，很容易出现三类问题：

- 分流不准：技术问题被普通客服回答，账单问题被技术 Agent 忽略。
- 上下文断裂：多轮对话里用户补充订单号、金额、错误码后，系统无法稳定延续。
- 难以迭代：没有评测、监控和规则热更新，项目只能“看起来能聊”，很难持续优化。

EchoMind 的目标是把这些能力做成一条完整链路。

## 核心处理链路

```text
用户请求
  -> /chat
  -> 读取 Redis 工作记忆、ChromaDB 历史摘要和用户画像
  -> 识别细粒度意图、意图组、置信度和结构化实体
  -> 按意图判断是否触发 RAG 知识库检索
  -> 生成结构化路由决策
     - primary_agent
     - supporting_agents
     - routing_reason
     - routing_confidence
  -> 调用对应 Agent
  -> 注入记忆、知识库、结构化实体和动态 Skills
  -> LLM 生成回复
  -> 写入工作记忆
  -> 异步更新用户画像
  -> Monitor 和 Evaluator 形成观测与评测闭环
```

## 核心技术亮点

### 1. 细粒度意图识别

EchoMind 不只识别“咨询、投诉、技术、账单”这类粗粒度意图，还支持更贴近业务的细粒度分类。

例如：

| 细粒度意图 | 归一化意图组 | 示例 |
|---|---|---|
| `logistics` | `query` | 快递什么时候到 |
| `refund` | `billing` | 退款多久到账 |
| `invoice` | `billing` | 帮我开发票 |
| `payment_issue` | `billing` | 为什么重复扣款 |
| `technical_login` | `technical` | 登录一直报 401 |
| `technical_crash` | `technical` | 应用一直崩溃 |
| `human_handoff` | `escalation` | 我要找人工客服 |

意图识别使用三路融合：

- LLM：负责语义理解和上下文判断
- Embedding / 本地哈希向量：负责模板相似度匹配
- Pattern：负责关键词兜底

最终输出的不只是 intent，还包括：

- `intent_group`
- `intent_confidence`
- `intent_source_scores`
- `urgency`
- `entities`

### 2. 结构化多 Agent 路由

EchoMind 当前的多 Agent 路由不是简单“命中两个关键词就并行”。

系统会根据意图、关键词和结构化实体，为不同领域打分：

```text
general
technical
billing
```

最高分成为主处理 Agent：

```text
primary_agent
```

其他证据足够强的专业 Agent 会成为辅助 Agent：

```text
supporting_agents
```

例如用户说：

```text
登录一直 401，而且刚才还重复扣款了
```

可能得到：

```json
{
  "primary_agent": "technical",
  "supporting_agents": ["billing"],
  "agent_types": ["technical", "billing"],
  "routing_reason": "intent=technical_login, group=technical, primary=technical, supporting=billing, scores=[technical=1.00, billing=0.54, general=0.10]"
}
```

这样系统能表达“谁主处理、谁辅助处理、为什么这么路由”。

### 3. RAG 知识库增强

EchoMind 使用 ChromaDB 构建知识库，用于存放退款政策、配送说明、技术排障、会员规则等文档。

检索链路包括：

```text
原始问题
  -> 查询改写
  -> 多子查询并行召回
  -> 合并去重
  -> LLM 重排
  -> Top-K 注入 Agent 上下文
```

但不是所有请求都会触发 RAG。

系统会先识别意图，只有业务类问题才检索知识库。问候、反馈、转人工、未知意图不会触发 RAG，避免无效检索和上下文干扰。

### 4. Redis + ChromaDB 记忆体系

EchoMind 把记忆拆成三层：

| 记忆类型 | 存储 | 作用 |
|---|---|---|
| 工作记忆 | Redis | 当前会话最近消息 |
| 情景记忆 | ChromaDB `episodic` | 历史对话摘要，支持语义检索 |
| 用户画像 | ChromaDB `user_profile` | 用户偏好和关键实体 |

Redis 读写使用异步客户端，ChromaDB 的同步操作放入线程池，减少主请求链路阻塞。

当当前会话消息过多时，系统会压缩旧消息，生成摘要，保留最近几轮对话，避免上下文无限膨胀。

### 5. 动态 Skills 注入

知识库解决的是“业务事实是什么”，Skills 解决的是“客服应该怎么处理”。

例如：

- 技术支持需要先收集错误码、版本、操作步骤
- 账单退款不能承诺立即到账
- 通用客服需要先澄清用户诉求
- 涉及敏感信息时需要提醒用户不要公开密码或验证码

EchoMind 支持从 `skills/` 目录加载 Markdown / JSON / TXT 规则文件，并根据 Agent 类型和关键词动态注入到 system prompt。

修改规则后可以通过接口热加载，不需要重启服务。

### 6. MCP 工具可靠性治理

EchoMind 把知识库检索封装成工具，并加入完整的可靠性机制：

- 参数校验
- TTL 缓存
- 超时控制
- 熔断器
- fallback 降级
- 查询改写
- LLM 重排
- 工具成功率和延迟统计

这让工具调用不只是“能调”，还具备可治理、可观测和可降级能力。

### 7. Monitor 在线观测和路由降权

Monitor 会定期采集：

- Agent 成功率
- Agent 平均延迟
- 工具成功率
- 工具平均延迟
- 连续失败次数
- 熔断状态

如果某个 Agent 表现变差，Monitor 会写回 `monitor_penalty`，影响后续 `routing_score`。

也就是说，监控不只是展示指标，还会影响后续路由选择。

### 8. LLM-as-Judge 端到端评测

EchoMind 内置 `/eval/run` 评测入口。

评测内容包括：

- 意图识别 Accuracy
- Macro-F1
- 端到端 Agent 回复质量
- LLM-as-Judge 四维评分
- 回归检测
- 优化建议

LLM-as-Judge 会从四个维度评价回复：

- 相关性
- 准确性
- 完整性
- 有用性

这让项目不只是“能回答”，而是能持续评估回答质量。

## 为什么它不是普通客服 Demo

普通客服 Demo 通常只有：

```text
用户输入 -> LLM 回复
```

EchoMind 则是：

```text
用户输入
  -> 意图识别
  -> 实体提取
  -> 记忆读取
  -> 按意图 RAG
  -> 结构化多 Agent 路由
  -> Skills 注入
  -> Agent 回复
  -> 记忆写入
  -> 画像更新
  -> 监控反馈
  -> 评测回归
```

它更像一个小型的 Multi-Agent Runtime，而不是单轮聊天机器人。

## 适合怎么介绍

如果面向技术同学，可以这样介绍：

> EchoMind 是一个支持 RAG、记忆增强、动态 Skills、结构化路由和评测闭环的多 Agent 客服编排运行时。

如果面向业务同学，可以这样介绍：

> EchoMind 可以把复杂客服问题自动分流给不同专业 Agent，结合企业知识库和历史上下文生成更稳定的回复，并通过监控和评测持续优化效果。

如果面向面试官，可以这样介绍：

> 我做的不是单个客服 Agent，而是一个 Multi-Agent Harness。它把意图识别、RAG、记忆、多 Agent 编排、工具可靠性、运行时监控和 LLM-as-Judge 评测串成了一条完整工程链路。
