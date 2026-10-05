# CompanyAgent

[English](#english) | [中文](#中文)

---

## English

An enterprise customer-service agent platform: one product, **three interchangeable backends** (Python, Java, .NET) sharing the same API contract, a Vue frontend that switches between them, and a full DevOps pipeline to Azure.

### Architecture

```text
User ─► Caddy (HTTPS) ─┬─► /              Vue frontend
                       ├─► /api/python/*  FastAPI        :8000
                       ├─► /api/java/*    Spring Boot    :8080
                       └─► /api/dotnet/*  ASP.NET Core   :8090
                                   │
                         Redis (working memory) + ChromaDB (RAG / long-term memory)
```

Request flow (`POST /chat`): load memory → recognize intent → retrieve knowledge (query rewrite, hybrid recall, LLM rerank) → route to General / Technical / Billing agent → LLM reply → verify answer → persist memory and update user profile asynchronously.

### Repository layout

| Path | What it is |
|---|---|
| `CompanyAgent/` | Python backend — FastAPI, Anthropic SDK, Redis, ChromaDB, OpenTelemetry, eval suite |
| `CompanyAgentJava/` | Java backend — Java 21, Spring Boot 3.5, Spring AI |
| `CompanyAgentDotnet/` | .NET backend — .NET 8, ASP.NET Core, Polly |
| `CompanyAgentFrontend/` | Vue 3 + Vite frontend with backend switcher |
| `*/skills/` | Business skills as hot-reloadable `SKILL.md` files |
| `deploy/` | Production Docker Compose + Caddy, Kubernetes manifests (kustomize) |
| `infra/terraform/` | Azure infrastructure (VM, network, NSG, monitoring alerts) |
| `.github/workflows/` | CI, CD, and LLM eval gate |
| `docs/` | Deployment runbook, DevOps & observability, lessons learned |

### Key features

- **Multi-agent routing** with hybrid intent recognition and skill-based prompts
- **RAG** with query rewriting, BM25 + vector recall, LLM rerank, caching and circuit breakers
- **Layered memory**: Redis working memory, episodic memory, async user profiles
- **Answer verification** with human-handoff signal and LLM fallback (Claude / DeepSeek)
- **Observability**: Prometheus metrics, Grafana dashboards, alert rules, OpenTelemetry traces (Jaeger)
- **Evaluation**: end-to-end eval set with LLM-as-judge, thresholds and baseline regression checks

### Quick start

Requires Docker and an Anthropic (or Anthropic-compatible) API key.

```bash
# Python backend only (+ Redis, ChromaDB, Prometheus, Grafana, Jaeger)
cd CompanyAgent
cp .env.example .env          # set ANTHROPIC_API_KEY
docker compose up -d --build
# API docs: http://localhost:8000/docs
```

```bash
# Full stack (all three backends + frontend behind Caddy); also needs CompanyAgent/.env from above
cp deploy/.env.production.example deploy/.env.production   # set DOMAIN, REDIS_PASSWORD
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production up -d --build
# add --profile observability for Prometheus / Grafana / Jaeger
```

See each subproject's README for running it standalone.

### CI/CD

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yml` | PR, push to `main` | Ruff + pytest, Terraform validate, kubeconform + compose validation, build all 4 images |
| `deploy.yml` | push to `main` | Run CI → push images to GHCR → SSH deploy to Azure VM → smoke test → **auto-rollback on failure** |
| `eval.yml` | PR touching agents/prompts, daily | Run eval set against the real model; fail on threshold breach or baseline regression |

### Docs

- [Deployment runbook](docs/DEPLOYMENT_RUNBOOK.md)
- [DevOps & observability](docs/DEVOPS_AND_OBSERVABILITY.md)
- [Lessons learned](docs/LESSONS_LEARNED.md)
- [Terraform](infra/terraform/README.md) · [Kubernetes](deploy/k8s/README.md)

---

## 中文

企业级智能客服 Agent 平台：同一个产品，**三套可互换的后端**（Python、Java、.NET）共用同一套 API 契约，Vue 前端可在三者之间切换，并配有到 Azure 的完整 DevOps 流水线。

### 架构

```text
用户 ─► Caddy (HTTPS) ─┬─► /              Vue 前端
                       ├─► /api/python/*  FastAPI        :8000
                       ├─► /api/java/*    Spring Boot    :8080
                       └─► /api/dotnet/*  ASP.NET Core   :8090
                                   │
                         Redis（工作记忆）+ ChromaDB（RAG / 长期记忆）
```

请求链路（`POST /chat`）：读取记忆 → 意图识别 → 知识检索（查询改写、混合召回、LLM 重排）→ 路由到 General / Technical / Billing Agent → LLM 生成回复 → 回答校验 → 写入记忆并异步更新用户画像。

### 目录结构

| 路径 | 说明 |
|---|---|
| `CompanyAgent/` | Python 后端 —— FastAPI、Anthropic SDK、Redis、ChromaDB、OpenTelemetry、评测集 |
| `CompanyAgentJava/` | Java 后端 —— Java 21、Spring Boot 3.5、Spring AI |
| `CompanyAgentDotnet/` | .NET 后端 —— .NET 8、ASP.NET Core、Polly |
| `CompanyAgentFrontend/` | Vue 3 + Vite 前端，支持切换后端 |
| `*/skills/` | 业务技能，以可热加载的 `SKILL.md` 定义 |
| `deploy/` | 生产环境 Docker Compose + Caddy、Kubernetes 清单（kustomize） |
| `infra/terraform/` | Azure 基础设施（VM、网络、NSG、监控告警） |
| `.github/workflows/` | CI、CD、LLM 评测卡点 |
| `docs/` | 部署手册、DevOps 与可观测性、踩坑记录 |

### 核心特性

- **多 Agent 路由**：混合意图识别 + 基于 Skill 的提示词
- **RAG**：查询改写、BM25 + 向量召回、LLM 重排、缓存与熔断
- **分层记忆**：Redis 工作记忆、情景记忆、异步用户画像
- **回答校验**：转人工信号，LLM 降级（Claude / DeepSeek）
- **可观测性**：Prometheus 指标、Grafana 看板、告警规则、OpenTelemetry 链路追踪（Jaeger）
- **评测**：端到端评测集 + LLM-as-Judge，阈值与基线退化检测

### 快速开始

需要 Docker 和 Anthropic（或兼容 Anthropic 协议的）API Key。

```bash
# 仅 Python 后端（含 Redis、ChromaDB、Prometheus、Grafana、Jaeger）
cd CompanyAgent
cp .env.example .env          # 填写 ANTHROPIC_API_KEY
docker compose up -d --build
# API 文档：http://localhost:8000/docs
```

```bash
# 全栈（三套后端 + 前端，经 Caddy 反代）；同样需要上面的 CompanyAgent/.env
cp deploy/.env.production.example deploy/.env.production   # 填写 DOMAIN、REDIS_PASSWORD
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production up -d --build
# 加 --profile observability 启用 Prometheus / Grafana / Jaeger
```

单独运行某个子项目请看其目录下的 README。

### CI/CD

| 工作流 | 触发 | 内容 |
|---|---|---|
| `ci.yml` | PR、推送到 `main` | Ruff + pytest、Terraform 校验、kubeconform + Compose 校验、构建 4 个镜像 |
| `deploy.yml` | 推送到 `main` | 跑 CI → 推镜像到 GHCR → SSH 部署到 Azure VM → 冒烟测试 → **失败自动回滚** |
| `eval.yml` | 改动 Agent/提示词的 PR、每日定时 | 用真实模型跑评测集，低于阈值或相对基线退化则失败 |

### 文档

- [部署手册](docs/DEPLOYMENT_RUNBOOK.md)
- [DevOps 与可观测性](docs/DEVOPS_AND_OBSERVABILITY.md)
- [踩坑记录](docs/LESSONS_LEARNED.md)
- [Terraform](infra/terraform/README.md) · [Kubernetes](deploy/k8s/README.md)
