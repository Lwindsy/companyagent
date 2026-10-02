# CompanyAgent on Kubernetes

当前生产环境仍是 Azure VM + Docker Compose（成本最低）。这套 manifests 是同一套服务的 Kubernetes 版本，
可以在本地 kind 集群跑通，迁移到 AKS 时只需要换镜像地址、加 TLS。

```
deploy/k8s/
├── base/                 所有环境共用
│   ├── configmap.yaml    非敏感配置（敏感值走 Secret companyagent-secrets）
│   ├── redis.yaml        StatefulSet + PVC
│   ├── chromadb.yaml     StatefulSet + PVC
│   ├── backend.yaml      Python API：Deployment + Service + HPA + PDB，startup/readiness/liveness 探针
│   ├── java.yaml         Spring Boot API
│   ├── frontend.yaml     nginx 前端（内部反代 /api/*）
│   └── ingress.yaml      ingress-nginx
└── overlays/
    ├── kind/             本地镜像 + 单副本
    └── production/       GHCR 镜像（CI 用 kustomize edit set image 改 tag）
```

## 在本地 kind 集群运行

需要 Docker、[kind](https://kind.sigs.k8s.io/)、kubectl。

```bash
# 1. 建集群并安装 ingress-nginx
kind create cluster --name companyagent
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.0/deploy/static/provider/kind/deploy.yaml

# 2. 构建镜像并加载到 kind（项目根目录执行）
docker build -t companyagent/companyagent-backend:dev --target production CompanyAgent
docker build -t companyagent/companyagent-java:dev CompanyAgentJava
docker build -t companyagent/companyagent-frontend:dev CompanyAgentFrontend
kind load docker-image companyagent/companyagent-backend:dev companyagent/companyagent-java:dev companyagent/companyagent-frontend:dev --name companyagent

# 3. Secret（从现有 .env 生成，不进 git）
kubectl create namespace companyagent
kubectl -n companyagent create secret generic companyagent-secrets --from-env-file=CompanyAgent/.env

# 4. 部署并等待就绪
kubectl apply -k deploy/k8s/overlays/kind
kubectl -n companyagent rollout status deploy/companyagent-backend --timeout=5m

# 5. 访问
kubectl -n companyagent port-forward svc/frontend 8080:80
# 浏览器打开 http://localhost:8080
```

`CompanyAgent/.env` 里必须有 `ANTHROPIC_API_KEY` 和 `REDIS_PASSWORD`。

## 设计说明

| 问题 | 做法 |
|---|---|
| 启动慢（加载 Chroma、skills） | `startupProbe` 最多等 3 分钟，之后才由 `livenessProbe` 接管，避免启动期间被误杀 |
| 发布不中断 | `maxUnavailable: 0` 滚动更新 + `PodDisruptionBudget minAvailable: 1` |
| 扩缩容 | HPA 2→5（CPU）。LLM 调用是 I/O 密集型，更好的指标是请求速率，可以用 KEDA 读 Prometheus 的 `companyagent_chat_requests_total` |
| 管理员 token 存在进程内存 | Ingress 开 cookie 粘性会话；彻底解决需要把会话存到 Redis |
| 密钥 | 只放在 Secret；AKS 上可以换成 Azure Key Vault + Secrets Store CSI Driver |
| 前端代理写死了 `companyagent`、`companyagent-java` | Service 名与 compose 服务名保持一致，镜像不用改 |
