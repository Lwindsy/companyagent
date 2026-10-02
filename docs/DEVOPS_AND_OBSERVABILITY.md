# CompanyAgent：部署、CI/CD 与可观测性

这份文档说明 CompanyAgent 如何构建、测试、评测、部署和监控，以及每个设计决策背后的原因。

## 全景

```
 开发者 push / PR
      │
      ▼
 ┌─────────────────────────── GitHub Actions ───────────────────────────┐
 │ ci.yml      ruff + pytest（假 LLM） │ terraform validate │ kubeconform │
 │             4 个 Docker 镜像构建                                          │
 │ eval.yml    改 prompt/Agent/skills 时，用真实模型跑评测集 → 阈值 + 基线卡点   │
 │ deploy.yml  main → 推镜像到 GHCR（tag = git SHA）→ SSH 到 Azure VM          │
 │             → compose pull + up → 冒烟测试 → 失败自动回滚                    │
 └──────────────────────────────────────────────────────────────────────┘
      │
      ▼
 Azure VM（infra/terraform 管理）
   Caddy(HTTPS) → frontend → companyagent(Python) / companyagent-java / companyagent-dotnet
                              │            │
                              │            └─ OTLP traces ─► Jaeger ◄─┐
                              └─ /metrics ─► Prometheus ─► Grafana ───┘
                                             └─ alert rules
```

| 目录 | 内容 |
|---|---|
| `CompanyAgent/observability/telemetry.py` | OpenTelemetry tracing、LLM 指标、JSON 日志 |
| `CompanyAgent/tests/` | 单元测试（假 LLM，无网络） |
| `CompanyAgent/evaluation/run_eval.py` | CI 评测卡点 |
| `CompanyAgent/evaluation/datasets/`, `thresholds.json` | 评测集与阈值（随代码版本管理） |
| `CompanyAgent/config/alerts/`, `config/grafana/` | Prometheus 告警规则、Grafana 数据源和看板 |
| `deploy/docker-compose.prod.yml` | 生产编排；`observability` profile 是监控栈 |
| `deploy/k8s/` | Kubernetes 版本（Kustomize：kind / production） |
| `infra/terraform/` | Azure 基础设施 |
| `.github/workflows/` | CI / Eval / Deploy |

---

## 1. 可观测性

### 1.1 Tracing：一次请求里发生了什么

一次 `/chat` 会触发 3–7 次 LLM 调用。每个请求生成一棵 span 树：

```
POST /chat                                   (FastAPI 自动埋点，根 span)
├─ memory.get_context                         Redis + Chroma
├─ intent.recognize                           intent / confidence / cached
│   └─ chat deepseek-v4-pro                   gen_ai.usage.input_tokens=…
├─ rag.retrieve
│   ├─ chat deepseek-v4-pro   (rag.rewrite)
│   └─ chat deepseek-v4-pro   (rag.rerank)
├─ orchestrator.run                           route.primary / route.reason / escalated
│   ├─ agent.billing
│   │   └─ chat deepseek-v4-pro
│   └─ agent.technical                        （复合问题并行协作）
│       └─ chat deepseek-v4-pro
└─ memory.write
```

- **LLM span 遵循 OpenTelemetry GenAI 语义约定**（`gen_ai.system`、`gen_ai.request.model`、`gen_ai.usage.*`），
  所以 Jaeger、Grafana Tempo、Langfuse、Azure Monitor 都能识别。切换后端只需要改环境变量：
  ```bash
  OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318                              # 自建 Jaeger
  OTEL_EXPORTER_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel      # Langfuse
  OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64(pk:sk)>"
  ```
- **埋点方式**：启动时给 anthropic SDK 的 `AsyncMessages.create` 包一层（`instrument_anthropic()`），
  6 个调用点不用各写一遍埋点，只需要用 `with llm_component("rag.rerank"):` 标明自己是哪个环节。
  `contextvars` 会随 `asyncio.gather` 自动复制，所以并行的 Agent 不会串标签。
- **隐私**：默认**不记录** prompt 和回答内容，只记录 token 数和元数据；排查问题时可以临时设
  `OTEL_CAPTURE_LLM_CONTENT=true`（内容截断到 2000 字符）。
- **关联**：每个响应都带 `X-Trace-Id` 头；`LOG_FORMAT=json` 时每行日志都带 `trace_id`。
  用户反馈问题时，拿 trace_id 可以直接查到整条链路。

### 1.2 Metrics（Prometheus，`/metrics`）

| 指标 | 标签 | 用途 |
|---|---|---|
| `companyagent_llm_requests_total` | component, model, status | LLM 调用量、错误率（按环节拆分） |
| `companyagent_llm_latency_seconds` | component, model | 哪个环节慢 |
| `companyagent_llm_tokens_total` | component, model, direction | token 消耗 |
| `companyagent_llm_cost_usd_total` | component, model | 估算成本（价格表可用 `LLM_PRICING_JSON` 配置） |
| `companyagent_chat_requests_total` | agent, intent_group, escalated, knowledge_used | 业务量、转人工率、RAG 使用率 |
| `companyagent_chat_latency_seconds` | agent | 端到端延迟 |
| `companyagent_http_requests_total` / `_duration_seconds` | method, route, status | HTTP 层 RED 指标 |

标签只用低基数字段（不用 user_id、conv_id），`route` 用路由模板而不是原始路径，防止 Prometheus 时间序列数量暴涨。

Grafana 看板 `CompanyAgent — LLM Agent Overview`（`config/grafana/dashboards/`）一共 12 个面板：
请求量、p50/p95 延迟、LLM 错误率、24 小时花费，以及按环节拆分的延迟、token 和成本。

### 1.3 告警（`config/alerts/companyagent.yml`）

服务不可用、LLM 错误率 > 5%、`/chat` p95 > 20s、估算花费 > $2/h、HTTP 5xx > 2%。
平台层（VM CPU、内存）告警由 Terraform 创建的 Azure Monitor 负责。

### 1.4 在哪里看

```bash
# 本地：整套栈（含 Jaeger、Prometheus、Grafana）
cd CompanyAgent && docker compose up -d
# API http://localhost:8000/docs · Jaeger http://localhost:16686 · Grafana http://localhost:3000 (admin/admin)

# 生产：监控栈是可选 profile，端口只绑定 127.0.0.1
#   deploy/.env.production 里设置 OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318 和 GRAFANA_ADMIN_PASSWORD
docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml --profile observability up -d
ssh -L 3000:localhost:3000 -L 16686:localhost:16686 azureuser@<vm>   # 本机浏览器打开 localhost:3000
```

监控栈大约占 600 MB 内存。4 GB 的 VM 同时运行 Java、.NET、Python 三个后端时比较紧张，所以默认不启动。

---

## 2. 测试与评测

### 2.1 单元测试（每次提交）

`tests/conftest.py` 用 `httpx.MockTransport` 冒充 Anthropic API。请求走的是**真实的 SDK 和埋点代码**，
只是不出网、不花钱、结果可控。目前覆盖：

- 路由：意图映射、复合问题并行协作、CRITICAL 升级、低置信度先追问、专属 Agent 失败降级
- 可观测性：span 父子关系、GenAI 属性、token 计数、错误状态、日志里的 trace_id、`/metrics`、`X-Trace-Id`
- 评测卡点：阈值、基线退化、Judge 不可信

```bash
cd CompanyAgent && pip install -r requirements-dev.txt && pytest -q && ruff check .
```

### 2.2 Eval gate（改 prompt 时 + 每天定时）

单元测试只能证明"代码逻辑没坏"，证明不了"回答质量没变差"，所以需要用真实模型跑评测：

1. 评测集 `evaluation/datasets/`：20 条意图识别用例和 6 条对话用例（含 1 条多轮），**和代码一起做版本管理**
2. 指标：意图准确率 / Macro-F1；LLM-as-Judge 从相关性、准确性、完整性、有用性四个维度打分
3. 两道卡（`thresholds.json`）：
   - 绝对下限，例如 `intent_accuracy ≥ 0.80`
   - 相对基线（`evaluation/baseline.json`）退化不超过 5%
4. **Judge 失败率 > 20% 时判定评测无效**（例如 API 限流）。否则 Judge 失败时返回的默认 0.5 分会被当成真实分数
5. 报告写到 GitHub Job Summary，包括每个指标与基线的差值、失败用例，以及本次评测花了多少 token 和钱

```bash
python -m evaluation.run_eval                    # 退出码 1 = 未通过
python -m evaluation.run_eval --update-baseline  # 确认结果可接受后，把它提交为新基线
```

**每天定时跑**是因为模型供应商可能悄悄更新模型。代码没变、效果却变了，这种情况只有定时评测能发现。

---

## 3. CI/CD

| Workflow | 触发 | 做什么 |
|---|---|---|
| `ci.yml` | 每个 PR、main | ruff、pytest、terraform fmt/validate、kubeconform、compose config、4 个镜像构建（GHA 缓存） |
| `eval.yml` | 改 Agent/prompt/skills 的 PR、每天 05:00 UTC、手动 | Eval gate；没有 API Key（例如 fork 的 PR）时跳过并给出警告 |
| `deploy.yml` | main（非文档改动）、手动 | 复用 CI → 推送镜像 → 部署 → 冒烟测试 → 失败回滚 |

部署流程的要点：

- **镜像在 CI 构建，服务器只拉镜像**（`--no-build`）。生产环境不编译代码，发布更快，构建出的镜像也可复现
- **用 git SHA 打 tag**：每个运行中的版本都能对应到一次提交；回滚就是换回上一个 SHA
- **自动回滚**：服务器上记录 `.release-tag` 和 `.previous-release-tag`；冒烟测试 5 分钟不通过，就切回上一版本，并让 workflow 失败
- **`environment: production`**：可以在 GitHub 里设置"需要人工审批"才发布
- **密钥**：`.env` 只存在于服务器上，CI 不同步、不覆盖它。GHCR 用一次性的 `GITHUB_TOKEN` 登录，用完立即 logout
- **SSH 白名单**：NSG 只允许管理员 IP 时，workflow 用 OIDC 登录 Azure（没有长期密钥），临时给 runner 的 IP 加一条规则，结束后（包括失败时）删除

### 首次配置清单

1. 把整个目录（而不只是 `CompanyAgent/`）作为一个 git 仓库推到 GitHub。workflow 在根目录的 `.github/workflows/`
2. 生成一把部署专用的 SSH key，把公钥加到 VM 的 `~/.ssh/authorized_keys`
3. GitHub → Settings → Secrets and variables → Actions：
   - Variables：`AZURE_VM_HOST`、`AZURE_VM_USER`（可选）、`ANTHROPIC_MODEL`、`ANTHROPIC_BASE_URL`
   - Secrets：`AZURE_VM_SSH_KEY`、`AZURE_VM_KNOWN_HOSTS`（`ssh-keyscan <host>` 的输出）、`ANTHROPIC_API_KEY`
4. 第一次手动触发 `Eval gate`，勾选 `update_baseline`，下载产物里的 `baseline.json` 并提交
5. GHCR 镜像默认是私有的，workflow 已经处理登录；也可以在 Packages 页面把它们设为公开

---

## 4. 基础设施

- **Terraform**（`infra/terraform/`）：资源组、VNet、NSG、静态 IP + DNS、VM（cloud-init 安装 Docker）、托管身份、Azure Monitor 告警。
  现有 VM 的两种接管方式（蓝绿迁移 / import）见该目录的 README
- **Kubernetes**（`deploy/k8s/`）：同一套服务的 K8s 版本，带探针、HPA、PDB 和滚动更新策略，可在 kind 上运行。
  见该目录的 README

---

## 5. 面试常见问题

**Q：为什么生产环境用 VM + Compose，而不是 K8s？**
成本和复杂度。单 VM 每月花费约为 AKS 的 1/5，当前流量不需要弹性。K8s manifests 已经准备好，
迁移的触发条件是需要多副本高可用或自动扩缩容。

**Q：LLM 应用的可观测性和普通 Web 服务有什么不同？**
① 一个请求里有多次模型调用，需要按环节拆开看延迟和 token；② 成本是一等指标；
③ "成功"不等于"答对"，质量要靠离线评测和 LLM-as-Judge；④ prompt 和回答可能含个人信息，默认不应该记录。

**Q：怎么防止改 prompt 把效果改差？**
Eval gate：固定评测集 + 绝对阈值 + 相对基线，PR 不通过就不能合并；每天定时跑，捕捉模型供应商侧的变化。

**Q：LLM-as-Judge 可靠吗？**
有偏差（偏好长回答、偏好同家族模型）。做法是固定 `temperature=0`；Judge 失败时判定评测无效，
而不是给默认分；定期抽样和人工标注做比对校准。

**Q：为什么 HPA 用 CPU 不太合适？**
LLM 调用的瓶颈在等待网络 I/O，CPU 很低但延迟很高。更好的扩缩指标是请求速率或并发数，可以用 KEDA 读 Prometheus 指标。

**Q：多副本有什么问题？**
管理员 token 和 Agent 路由统计存在进程内存里。目前用 Ingress 粘性会话绕过，正确的做法是把它们存到 Redis。

**Q：部署失败怎么办？**
冒烟测试失败会自动切回上一个镜像 SHA。数据在 volume 里，回滚不影响数据。手动回滚就是把 `IMAGE_TAG` 设成任意历史 SHA 再执行 `up -d`。
