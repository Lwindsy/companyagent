# EchoMind

企业级智能客服系统，基于 Anthropic Claude API 构建，聚焦六个核心技术亮点。

```
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
   ╔══════════════════════╗
   ║   EchoMind  v2.0     ║
   ║   智能客服 AI 系统    ║
   ╚══════════════════════╝
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
```

---

## 技术亮点

### 1. 端到端意图识别 `core/intent_recognizer.py`

三路融合策略，LLM 和 Embedding **并行**调用，不串行等待：

```
用户消息
    ├── LLM 语义理解（70%）   ← Few-shot + 对话上下文，理解复杂语义
    ├── Embedding 相似度（20%）← voyage-3-lite，快速匹配常见表达
    └── 关键词模式匹配（10%） ← 零延迟兜底

        ↓ 加权投票合并
    最终意图 + 置信度 + 紧急度 + 实体提取
```

支持在线学习：用户纠正后自动更新模板，清除对应 Embedding 缓存。

---

### 2. MCP 工具调用框架 `mcp/tool_manager.py`

解决检索类工具的两个核心问题：

**召回不全 → 查询改写（Query Rewriting）**
```
原始查询: "退款流程"
    ↓ LLM 改写为 3 个角度
子查询: ["如何申请退款", "退款需要多少天", "退款政策是什么"]
    ↓ 并行检索 + 合并去重
合并结果（覆盖更全面）
```

**召回不好 → LLM 重排（Reranking）**
```
合并结果（向量相似度排序，不等于"对用户有用"）
    ↓ LLM 按语义相关性打分重排
Top-K 结果（质量显著提升）
```

其他可靠性保障：熔断器（连续失败自动断开）、TTL 缓存、降级策略。

---

### 3. 多轮对话记忆管理 `memory/conversation_memory.py`

三级记忆架构，模拟人类记忆机制：

| 层级 | 存储 | 内容 | 特点 |
|------|------|------|------|
| 工作记忆 | Redis | 当前会话最近 20 条消息 | 毫秒级读写，24h TTL |
| 情景记忆 | ChromaDB | 历史对话压缩摘要 | 语义向量检索，跨会话关联 |
| 用户画像 | ChromaDB | 偏好、常用实体 | 从对话中自动提炼，持久化 |

**自动压缩**：工作记忆达到阈值时，LLM 生成摘要 → 存入情景记忆 → 工作记忆只保留最近 5 条，防止 context 爆炸。

---

### 4. 多 Agent 路由与编排 `agents/agent_orchestrator.py`

三层路由决策：

```
用户请求
    ↓
1. 意图映射路由
   TECHNICAL  → TechnicalAgent（故障排查、错误诊断）
   BILLING    → BillingAgent（账单、退款、发票）
   ESCALATION → 直接升级
   其他       → GeneralAgent

    ↓
2. 性能路由（同类多实例时）
   routing_score = 成功率 × 0.7 + 低延迟分 × 0.3
   选得分最高的实例

    ↓
3. 降级路由
   专属 Agent 失败 → 自动降级到 GeneralAgent
```

支持**并行协作**：复杂问题（如同时涉及技术和账单）可同时派发给多个 Agent，结果合并后返回。

---

### 5. Monitor 监控 Agent 在线表现 `monitor/performance_monitor.py`

Monitor 与 Orchestrator 形成**闭环反馈**：

```
Orchestrator 处理请求
    ↓ 实时更新 AgentStats（成功率、延迟）
Monitor 每 10s 采集
    ↓ Z-score 异常检测 + 阈值告警
发现某 Agent 成功率下降
    ↓ 写入日志 + 可选 Webhook
Orchestrator._best_agent() 读取 routing_score
    ↓ routing_score = 成功率 × 0.7 + 低延迟分 × 0.3
自动降低问题 Agent 的路由权重 ← 闭环完成
```

Monitor 不需要额外埋点，直接读取 Orchestrator 和 ToolManager 的运行时统计。

---

### 6. 端到端评测框架 `evaluation/evaluator.py`

**LLM-as-Judge**：用 LLM 评判 Agent 响应质量，可规模化、可重复。

```
评测维度：
  意图识别  → Accuracy + Macro-F1（纯 Python 计算，无需 sklearn）
  响应质量  → LLM Judge 打分（相关性、准确性、完整性、有用性）
  回归检测  → 与历史基线对比，退化超 5% 自动标记
  优化建议  → 基于评分自动生成可操作建议
```

内置开箱即用的测试用例，`POST /eval/run` 一键触发。

---

## 项目结构

```
EchoMind/
├── api/
│   └── main.py                 # FastAPI 入口，完整请求链路
├── core/
│   └── intent_recognizer.py    # 三路融合意图识别
├── agents/
│   └── agent_orchestrator.py   # 多 Agent 路由与编排
├── memory/
│   └── conversation_memory.py  # 三级记忆管理
├── mcp/
│   └── tool_manager.py         # MCP 工具框架（含查询改写+重排）
├── monitor/
│   └── performance_monitor.py  # 在线表现监控
├── evaluation/
│   └── evaluator.py            # 端到端评测（LLM-as-Judge）
├── config/
│   ├── prometheus.yml
│   └── nginx/nginx.conf
├── Dockerfile
├── docker-compose.yml
└── .env
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/chat` | 主对话接口，完整链路 |
| `GET` | `/health` | 健康检查 |
| `GET` | `/monitor` | 实时监控摘要（Agent 成功率、告警、优化建议） |
| `POST` | `/search?query=xxx` | 演示检索优化链路（查询改写 + 重排） |
| `POST` | `/eval/run` | 运行内置评测用例，返回评测报告 |
| `GET` | `/docs` | Swagger UI |

### 对话示例

```bash
curl -X POST http://localhost/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "我的订单 #12345 还没到，已经超时了",
    "user_id": "user_001",
    "conv_id": "conv_abc"
  }'
```

```json
{
  "conv_id": "conv_abc",
  "response": "非常抱歉给您带来不便...",
  "intent": "query",
  "agent_type": "general",
  "escalated": false,
  "latency_ms": 842.3
}
```

---

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ANTHROPIC_API_KEY` | API Key（必填） | — |
| `ANTHROPIC_MODEL` | 模型名称 | `claude-3-5-sonnet-20241022` |
| `ANTHROPIC_BASE_URL` | 自定义 API 地址（DeepSeek 等） | 官方地址 |
| `REDIS_URL` | Redis 连接串 | `redis://redis:6379/0` |
| `CHROMA_PERSIST_DIRECTORY` | ChromaDB 数据目录 | `/app/data/chroma` |
| `PROMETHEUS_PORT` | Prometheus 指标端口 | `9091` |
| `MONITOR_INTERVAL` | 监控采集间隔（秒） | `10` |
| `LOG_LEVEL` | 日志级别 | `INFO` |

---

## 技术栈

| 层 | 技术 |
|----|------|
| LLM | Anthropic Claude / DeepSeek（兼容 Anthropic 协议） |
| Embedding | Anthropic voyage-3-lite |
| API 框架 | FastAPI + Uvicorn |
| 工作记忆 | Redis |
| 向量存储 | ChromaDB |
| 监控 | Prometheus |
| 容器化 | Docker + Docker Compose |
| 反向代理 | Nginx |

---

## 常用运维命令

```bash
# 查看服务状态
docker compose ps

# 查看实时日志
docker compose logs -f echomind

# 重启应用
docker compose restart echomind

# 停止（保留数据）
docker compose down

# 停止并清除所有数据
docker compose down -v

# 进入容器调试
docker compose exec echomind bash
```
