# EchoMind

企业级智能客服系统，基于 LLM 构建，支持 Anthropic Claude 和 DeepSeek 等兼容 API。

```
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
   ╔══════════════════════╗
   ║   EchoMind  v2.0     ║
   ║   智能客服 AI 系统    ║
   ╚══════════════════════╝
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
```

---

## 六大技术亮点

### 1. 端到端意图识别 `core/intent_recognizer.py`

三路融合 + 加权投票，LLM 和 Embedding 并行调用：

```
用户消息 + 对话历史
    ├── LLM 语义理解（85%）  ← Few-shot + 多轮上下文
    ├── Embedding 相似度（—） ← 官方 API 时 20%，第三方时禁用
    └── 关键词模式匹配（15%） ← 零延迟兜底
        ↓ 加权投票
    意图 + 置信度 + 紧急度 + 实体
```

### 2. MCP 工具调用 + RAG 知识库 `mcp/tool_manager.py` + `mcp/knowledge_base.py`

真实的 ChromaDB 向量检索，解决召回不全和排序差：

```
查询改写（3 个角度）→ 并行召回 ChromaDB → 合并去重 → LLM 重排 → Top-K
```

可靠性保障：熔断器 + TTL 缓存 + 参数校验 + 降级策略。

### 3. 三级记忆管理 `memory/conversation_memory.py`

| 层级 | 存储 | 内容 |
|------|------|------|
| 工作记忆 | Redis | 当前会话最近 20 条，24h TTL |
| 情景记忆 | ChromaDB | 历史对话 LLM 摘要，语义检索 |
| 用户画像 | ChromaDB | 从对话中自动提炼偏好和实体 |

超过 15 条时自动压缩（LLM 摘要 → 存入情景记忆 → 保留最近 5 条）。

### 4. 多 Agent 路由编排 `agents/agent_orchestrator.py`

三层路由：意图映射 → 性能路由（routing_score）→ 降级兜底。支持并行协作。

### 5. Monitor 闭环监控 `monitor/performance_monitor.py`

每 10s 采集 Agent/工具统计 → Z-score 异常检测 → 告警 → routing_score 自动降权。

### 6. 端到端评测 `evaluation/evaluator.py`

真正调用 Orchestrator 产出回复 → LLM-as-Judge 四维度打分 → 回归检测 → 优化建议。

---

## 项目结构

```
EchoMind/
├── api/main.py                    # FastAPI 入口，完整请求链路
├── core/intent_recognizer.py      # 三路融合意图识别
├── agents/agent_orchestrator.py   # 多 Agent 路由编排
├── memory/conversation_memory.py  # 三级记忆管理
├── mcp/
│   ├── tool_manager.py            # MCP 工具框架（查询改写 + 重排）
│   └── knowledge_base.py          # RAG 知识库（ChromaDB 向量检索）
├── monitor/performance_monitor.py # 在线表现监控
├── evaluation/evaluator.py        # 端到端评测（LLM-as-Judge）
├── data/demo_docs/                # 演示文档（可通过 API 导入）
├── config/
│   ├── prometheus.yml
│   └── nginx/nginx.conf
├── wiki/                          # 完整文档（部署/亮点/代码讲解/简历）
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env
```

---

## 快速部署

### 前置条件

- Docker & Docker Compose
- API Key（Anthropic 或 DeepSeek）

### 方式一：docker compose（完整栈）

```bash
# 编辑 .env 填写 API Key
vim .env

# 构建并启动（Redis + ChromaDB + Prometheus + EchoMind + Nginx）
docker compose up -d --build

# 验证
curl http://localhost:8000/health
```

### 方式二：docker run（开发模式，代码挂载）

```bash
# 先启动依赖
docker compose up -d redis chromadb

# 构建镜像
docker compose build --no-cache echomind

# 启动 HTTP 服务器
docker run -it --rm \
  --network echomind_echomind-network \
  -p 8000:8000 \
  -e ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic" \
  -e ANTHROPIC_API_KEY="your_key" \
  -e ANTHROPIC_MODEL="deepseek-chat" \
  -e REDIS_URL="redis://:echomind123@redis:6379/0" \
  -e CHROMA_HOST="chromadb" \
  -e CHROMA_PORT="8000" \
  -e CHROMA_PERSIST_DIRECTORY="/workspace/data/chroma" \
  -v $(pwd):/workspace \
  -w /workspace \
  echomind
```

### 方式三：CLI 交互模式

末尾加 `python api/main.py --cli`。

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/chat` | 主对话接口 |
| `GET` | `/health` | 健康检查 |
| `GET` | `/monitor` | Agent/工具实时监控 + 告警 + 优化建议 |
| `POST` | `/search?query=xxx` | 检索优化演示（查询改写 + 重排） |
| `POST` | `/knowledge/add` | 批量导入文档到知识库 |
| `POST` | `/knowledge/upload` | 上传文件导入知识库（.txt/.md/.json） |
| `GET` | `/knowledge/stats` | 知识库统计 |
| `POST` | `/eval/run` | 运行端到端评测 |
| `GET` | `/docs` | Swagger UI |

---

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ANTHROPIC_API_KEY` | API Key（必填） | — |
| `ANTHROPIC_MODEL` | 模型名称 | `claude-3-5-sonnet-20241022` |
| `ANTHROPIC_BASE_URL` | 第三方 API 地址 | 官方地址 |
| `REDIS_URL` | Redis 连接串 | `redis://redis:6379/0` |
| `CHROMA_HOST` | ChromaDB 服务地址 | `chromadb` |
| `CHROMA_PORT` | ChromaDB 端口 | `8000` |
| `CHROMA_PERSIST_DIRECTORY` | ChromaDB 本地降级路径 | `/app/data/chroma` |
| `PROMETHEUS_PORT` | Prometheus 端口（0=不启动） | `9091` |
| `MONITOR_INTERVAL` | 监控采集间隔（秒） | `10` |

---

## 技术栈

| 层 | 技术 |
|----|------|
| LLM | Anthropic Claude / DeepSeek |
| 向量存储 | ChromaDB（记忆 + RAG 知识库） |
| 工作记忆 | Redis |
| API 框架 | FastAPI + Uvicorn |
| 监控 | Prometheus（可选） |
| 容器化 | Docker + Docker Compose |
| 反向代理 | Nginx |
