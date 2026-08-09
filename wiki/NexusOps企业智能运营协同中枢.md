# NexusOps 企业智能运营协同中枢包装

这份文档用于把 NexusOps 从“智能客服 Agent”升级包装成“企业智能运营协同中枢”。

## 一句话定位

NexusOps 是一个面向企业复杂运营场景的智能协同中枢，能够统一接入业务请求，自动识别意图、检索企业知识、分派专业 Agent、协同处理跨领域问题，并通过记忆、监控和评测机制持续优化处理质量。

更简洁的说法：

> NexusOps 是一个支持 RAG、记忆增强、结构化多 Agent 路由和评测闭环的企业智能运营协同中枢。

## 为什么不只叫智能客服

“智能客服”容易让人理解成：

```text
用户问一句
机器人答一句
```

但 NexusOps 当前项目已经包含：

- 细粒度业务意图识别
- 结构化实体提取
- RAG 企业知识库
- Redis + ChromaDB 记忆体系
- 多 Agent 主辅协同路由
- 动态 Skills 规则注入
- 工具熔断、缓存、fallback
- Monitor 在线观测和路由降权
- LLM-as-Judge 端到端评测

所以它更像一个企业运营场景下的 Agent 协同平台，而不是单个客服机器人。

## 业务背景

企业日常运营中会收到大量跨部门、跨系统的问题：

- 客户成功团队要查询订单、物流、会员、权益规则
- 技术支持团队要处理登录失败、错误码、系统崩溃
- 财务运营团队要处理退款、发票、重复扣款、支付失败
- 运营团队要维护最新政策、处理规范和升级边界
- 管理者需要观察 Agent 成功率、延迟、工具稳定性和回复质量

传统方式通常依赖人工分流：

```text
用户问题 -> 一线人员判断 -> 查知识库 -> 问技术/财务 -> 手工回复 -> 人工复盘
```

问题是：

- 分流慢
- 上下文容易丢
- 跨部门问题容易漏答
- 业务知识更新后不容易同步
- 没有统一评测和回归机制

NexusOps 的目标是把这个流程做成智能化、可观测、可评测的协同链路。

## 新业务场景定义

NexusOps 可以包装成：

```text
企业智能运营协同中枢
```

它统一处理企业运营中的复杂请求，包括：

| 场景 | 示例 | 处理方式 |
|---|---|---|
| 订单履约 | 订单什么时候到、物流多久更新 | 识别 `logistics/order_status`，查询知识库，由运营协调 Agent 处理 |
| 技术故障 | 登录 401、页面 500、系统崩溃 | 识别 `technical_login/technical_crash`，路由到技术可靠性 Agent |
| 账务异常 | 重复扣款、支付失败、退款不到账 | 识别 `payment_issue/refund`，路由到收入与合规 Agent |
| 发票处理 | 开票、改抬头、税号问题 | 识别 `invoice`，路由到收入与合规 Agent |
| 复合问题 | 登录报错且重复扣款 | 生成主 Agent + 辅助 Agent，协同处理 |
| 升级诉求 | 我要转人工、我要投诉 | 识别 `human_handoff/escalation`，触发升级标记 |

## Agent 角色重新包装

原来的 General、Technical、Billing Agent 可以换成更偏企业运营的名字。

| 代码中的 Agent | 包装后的角色 | 说明 |
|---|---|---|
| `GeneralAgent` | 运营协调 Agent | 处理通用咨询、订单物流、会员权益、信息澄清 |
| `TechnicalAgent` | 技术可靠性 Agent | 处理登录失败、错误码、崩溃、系统异常 |
| `BillingAgent` | 收入与合规 Agent | 处理退款、发票、支付异常、订阅、账务边界 |
| `ESCALATION` | 运营升级通道 | 标记高优先级问题，预留工单/人工队列接入 |

这样表达时，项目会从“客服机器人”升级为：

```text
多角色企业运营 Agent 协同系统
```

## 核心链路

```text
业务请求
  -> /chat 统一入口
  -> 读取工作记忆、历史摘要和用户画像
  -> 识别细粒度业务意图
  -> 提取结构化实体
  -> 按意图决定是否检索企业知识库
  -> 生成结构化路由决策
     - primary_agent
     - supporting_agents
     - routing_reason
     - routing_confidence
  -> 注入业务知识、历史上下文、结构化实体和动态 Skills
  -> 专业 Agent 生成回复
  -> 写入记忆
  -> 异步更新用户画像
  -> Monitor 观测运行状态
  -> Evaluator 评测回复质量和回归风险
```

## 技术能力和业务价值映射

| 技术能力 | 业务价值 |
|---|---|
| 细粒度意图识别 | 更准确判断问题属于订单、物流、退款、发票、技术故障还是转人工 |
| `intent_group` 归一化 | 同时保留细粒度业务语义和上层路由类别 |
| 结构化实体提取 | 自动识别订单号、金额、日期、错误码，减少反复追问 |
| 按意图触发 RAG | 业务类问题检索企业知识库，非业务问题不浪费检索成本 |
| 多 Agent 主辅路由 | 复合问题有主处理 Agent，也能让辅助 Agent 补充专业意见 |
| 动态 Skills | 运营规则、排障 SOP、账务边界可热加载，不必改代码 |
| Redis 工作记忆 | 当前会话保持连续性 |
| ChromaDB 长期记忆 | 支持历史摘要、用户画像和知识库语义检索 |
| MCP 工具治理 | 工具调用具备缓存、超时、熔断和降级能力 |
| Monitor 路由降权 | 表现差的 Agent 会被动态降低路由分数 |
| LLM-as-Judge 评测 | 对 Agent 回复质量做自动化评估和回归检测 |

## 多 Agent 协同示例

用户输入：

```text
登录一直 401，而且刚才还重复扣款了
```

系统识别：

```text
intent = technical_login
intent_group = technical
entities.error_code = ["401"]
```

领域打分：

```text
technical = 高
billing = 中高
general = 低
```

路由决策：

```json
{
  "primary_agent": "technical",
  "supporting_agents": ["billing"],
  "agent_types": ["technical", "billing"],
  "routing_reason": "用户主要诉求是登录 401，同时包含重复扣款线索"
}
```

回复形态：

```text
[technical - 主处理]
先解释 401 登录失败的可能原因，并给出排查步骤。

[billing - 辅助处理]
补充重复扣款的核验建议，提示保留支付流水，并说明退款需人工审核。
```


## 简历标题建议

可以使用：

```text
NexusOps 企业智能运营协同中枢
```

或者更偏技术：

```text
NexusOps 多 Agent 客服编排运行时
```

或者中英文混合：

```text
NexusOps: Multi-Agent Customer Support Harness
```

## 最适合强调的技术亮点

1. **不是单 Agent，而是 Multi-Agent Harness**
   - 支持主 Agent 和辅助 Agent
   - 支持路由原因和路由分数返回
   - 支持复合问题并行协作

2. **不是简单 RAG，而是按意图触发的 RAG**
   - 业务类问题检索知识库
   - 问候、反馈、转人工、未知意图不触发检索

3. **不是只会聊天，而是有评测闭环**
   - `/eval/run`
   - LLM-as-Judge
   - Accuracy / Macro-F1
   - 回归检测
   - 优化建议

4. **不是硬编码规则，而是动态 Skills**
   - Markdown 规则文件
   - Agent 隔离注入
   - 支持热加载

5. **不是临时上下文，而是分层记忆**
   - Redis 工作记忆
   - ChromaDB 情景记忆
   - ChromaDB 用户画像

## 标题

```text
NexusOps 企业智能运营协同中枢
```

副标题：

```text
支持 RAG、记忆增强、结构化多 Agent 路由和评测闭环的企业运营 Agent 平台
```
