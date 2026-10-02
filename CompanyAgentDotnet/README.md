# CompanyAgent .NET 版本说明

CompanyAgent .NET 是智能客服 Agent 平台的第三套后端，使用 C# / ASP.NET Core 8 实现，接口与 Python（FastAPI）版、Java（Spring Boot）版对齐，前端可以在三者之间切换。

## 技术栈

| 类型 | 技术 |
|------|------|
| 语言 / 框架 | C# 12、.NET 8、ASP.NET Core（Controllers） |
| LLM | 官方 Anthropic C# SDK（Claude）；DeepSeek 走 OpenAI 兼容 REST（`IHttpClientFactory`） |
| 弹性 | Polly v8（知识检索：超时 + 熔断）；`Microsoft.Extensions.Http.Resilience`（DeepSeek：重试、超时、熔断） |
| 记忆缓存 | StackExchange.Redis（工作记忆，MULTI/EXEC 事务） |
| RAG | BM25 + 本地 hash 向量融合检索 + LLM 查询改写 + LLM rerank |
| 后台任务 | `BackgroundService` + `Channel<T>`（用户画像异步更新）、`PeriodicTimer`（监控采集） |
| 监控 | prometheus-net（`/metrics`，含 HTTP 请求指标） |
| API 文档 | Swashbuckle / Swagger UI（`/docs`） |
| 测试 | xUnit + `WebApplicationFactory`（进程内集成测试） |
| 部署 | 多阶段 Dockerfile、Docker Compose、Caddy / Nginx 反向代理 |

## 目录

```text
CompanyAgentDotnet/
├── src/CompanyAgent.Api/
│   ├── Api/            控制器、DTO、管理员会话、异常处理
│   ├── Agents/         General / Technical / Billing Agent、编排器、回答校验
│   ├── Intent/         混合意图识别、实体抽取
│   ├── Knowledge/      知识库、切分器、BM25 与向量
│   ├── Tools/          knowledge_search 工具（改写、并行召回、rerank、缓存、熔断）
│   ├── Memory/         Redis 工作记忆、情景记忆、用户画像队列
│   ├── Skills/         SKILL.md 热加载
│   ├── Evaluation/     端到端评测、LLM-as-Judge
│   ├── Monitoring/     监控采集、告警、路由惩罚
│   ├── Llm/            Claude / DeepSeek 网关与降级回复
│   └── Program.cs      依赖注入与中间件
├── tests/CompanyAgent.Api.Tests/
├── skills/             与 Java 版相同的业务 Skills
└── Dockerfile
```

## 核心链路

```text
POST /chat
  -> MemoryManager 读取 Redis 工作记忆、会话摘要、情景记忆、用户画像
  -> IntentRecognizer：LLM 投票(0.7) + 字符 n-gram 相似度(0.2) + 关键词规则(0.1)
  -> KnowledgeToolManager：查询改写 -> 多子查询并行召回 -> LLM rerank（Polly 超时 + 熔断）
  -> AgentOrchestrator：按意图、关键词、实体打分；强相关的辅助 Agent 用 Task.WhenAll 并行处理
  -> AnswerVerifier 校验回答是否可信、是否需要转人工
  -> 写回 Redis；用户画像更新放入 Channel，由后台 Worker 异步处理
```

## 与 Java 版的对照

| 能力 | Java 版 | .NET 版 |
|------|---------|---------|
| 接口路径与 snake_case 字段 | 已实现 | 已对齐（`conv_id` 与 `conversation_id` 都接受） |
| 模型调用 | Spring AI ChatModel | 官方 Anthropic SDK；按模型代际决定是否发送 temperature / effort；Claude API 上启用服务端 refusal fallback |
| 异步 | `CompletableFuture`、`@Async` | 全链路 `async/await`、`CancellationToken`；`Channel` + `BackgroundService` |
| 熔断 | 自写 CircuitBreaker | Polly `ResiliencePipeline` |
| 定时任务 | `@Scheduled` | `PeriodicTimer` + `BackgroundService` |
| 知识库并发 | `CopyOnWriteArrayList` | 不可变快照整体替换，检索不加锁；写入时预计算词频和向量 |
| 检索缓存 | 5 分钟 TTL，新增文档后仍可能返回旧结果 | 缓存 key 带知识库版本号，新增文档后自动失效 |
| 向量哈希 | `String.hashCode()` | FNV-1a（.NET 的 `string.GetHashCode()` 每个进程随机，不能用于持久化比较） |
| 持久化写入 | 直接覆盖 JSON | 先写临时文件再原子替换 |
| 管理员密码 | 代码内有默认值 | 无默认值，未配置 `ADMIN_PASSWORD` 时拒绝管理员登录 |
| Redis key | `java:wm:*` | `dotnet:wm:*`（与其他后端共用 Redis 互不覆盖） |
| ChromaDB | 未作为主检索源 | 同 Java，未接入 |

## 主要接口

默认端口：`8090`。除 `/health`、`/chat`、`/admin/login`、`/metrics`、`/docs` 外，其余接口需要 `Authorization: Bearer <token>`。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查与 Agent 路由统计 |
| POST | `/chat` | 主对话接口 |
| POST | `/admin/login` | 管理员登录，返回 8 小时有效的 token |
| GET | `/admin/overview` | 管理后台概览 |
| POST | `/search?query=&top_k=` | 知识库检索 |
| POST | `/knowledge/add` | 批量添加文档 |
| POST | `/knowledge/upload` | 上传 `.txt` / `.md` / `.json`（≤ 10MB） |
| GET | `/knowledge/stats` | 知识库片段数 |
| GET | `/monitor` | Agent / 工具统计、告警、建议 |
| GET | `/skills`、POST `/skills/reload` | 查看、热加载 Skills |
| POST | `/eval/run` | 评测；空请求体使用内置用例 |
| GET | `/metrics` | Prometheus 指标 |
| GET | `/docs` | Swagger UI（OpenAPI JSON：`/v3/api-docs/v1.json`） |

## 本地运行

需要 .NET 8 SDK。没有 Redis 时可以设置 `REDIS_ENABLED=false`，工作记忆会保存在进程内。

```powershell
cd CompanyAgentDotnet
$env:REDIS_ENABLED="false"
$env:ADMIN_PASSWORD="<自选密码>"
$env:ANTHROPIC_API_KEY="<你的 key>"   # 不填时返回本地降级回复
$env:COMPANYAGENT_SKILLS_DIR="skills"
dotnet run --project src/CompanyAgent.Api
```

- 健康检查：<http://localhost:8090/health>
- Swagger：<http://localhost:8090/docs>

前端本地开发时，Vite 会把 `/api/dotnet` 代理到 `http://localhost:8090`。

全部配置项见 `.env.example`，变量名与 Java / Python 版一致，可共用同一个 `.env`。

## 测试

```powershell
dotnet test
```

测试不调用真实模型，也不需要 Redis，覆盖：

- 意图识别：LLM 投票、关键词细化、LLM 失败时的降级、缓存、实体抽取
- Agent 编排：多 Agent 并行、紧急升级、澄清、路由惩罚
- 知识库：排序、持久化与重载、去重、切分、缓存失效、rerank
- 记忆压缩、Skills 解析、管理员会话过期、评测指标
- 发给 Claude 的真实请求 JSON（截获 SDK 请求，不访问网络）
- 通过 `WebApplicationFactory` 验证 HTTP 契约：snake_case 字段、鉴权、Swagger、指标

## Docker 部署

生产环境由 `deploy/docker-compose.prod.yml` 中的 `companyagent-dotnet` 服务构建，Caddy 把 `/api/dotnet/*` 转发到 `companyagent-dotnet:8090`，并通过 `X-Forwarded-Prefix` 让 Swagger 的 “Try it out” 使用正确的公网路径。首次上线步骤见根目录的 `DEPLOYMENT_GUIDE.md`。
