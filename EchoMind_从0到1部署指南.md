# EchoMind 从 0 到 1 部署指南

本文档面向第一次接触 EchoMind 的新手，按顺序执行即可在本机启动完整服务，并完成基础验证。

EchoMind 是一个基于 FastAPI 的智能客服系统，核心依赖包括：

- EchoMind API：主应用服务，默认端口 `8000`
- Redis：短期会话记忆，默认端口 `6379`
- ChromaDB：向量知识库和长期记忆，宿主机端口 `8001`
- Prometheus：监控服务，默认端口 `9090`
- Nginx：反向代理，默认端口 `80`

推荐新手优先使用 Docker Compose 部署，因为它会一次性启动主应用和所有依赖服务。

## 1. 部署前准备

### 1.1 安装基础工具

你需要先准备：

- Docker Desktop
- Docker Compose
- 一个可用的 Anthropic API Key，或兼容 Anthropic 协议的模型 API Key

安装完成后，在终端执行下面命令确认 Docker 可用：

```bash
docker --version
docker compose version
```

如果第二条命令不可用，说明 Docker Compose 没有正确安装或 Docker Desktop 没有启动。

### 1.2 进入项目目录

```bash
cd /Users/xiao_xiong/Desktop/code/EchoMind
```

确认当前目录下能看到这些文件：

```bash
ls
```

至少应包含：

```text
Dockerfile
docker-compose.yml
requirements.txt
.env.example
api/
agents/
core/
memory/
mcp/
skills/
```

## 2. 配置环境变量

### 2.1 复制环境变量模板

```bash
cp .env.example .env
```

如果 `.env` 已经存在，不要直接覆盖。可以先备份：

```bash
cp .env .env.backup
```

### 2.2 编辑 `.env`

打开 `.env`，至少修改模型 API Key：

```env
ANTHROPIC_API_KEY=你的真实_api_key
```

如果你使用官方 Anthropic，可以保持默认模型配置。

如果你使用兼容 Anthropic 协议的第三方服务，例如 DeepSeek，需要额外配置：

```env
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-v4-pro
ANTHROPIC_API_KEY=你的真实_api_key
```

Docker Compose 部署时，Redis 连接会由 `docker-compose.yml` 自动覆盖为容器内地址，通常不用手动改：

```env
REDIS_PASSWORD=echomind123
CHROMA_HOST=localhost
CHROMA_PORT=8001
```

注意：`.env` 内含密钥，不要提交到 Git，也不要发给别人。

## 3. 一键启动完整服务

在项目根目录执行：

```bash
docker compose up -d --build
```

第一次启动会构建镜像并下载依赖，耗时可能较长。该命令会启动：

```text
echomind-app
echomind-redis
echomind-chromadb
echomind-prometheus
echomind-nginx
```

查看容器状态：

```bash
docker compose ps
```

正常情况下，服务状态会逐步变为 `healthy` 或 `Up`。

查看主应用日志：

```bash
docker compose logs -f echomind
```

看到类似 `EchoMind 已就绪` 的日志后，说明主服务初始化完成。

## 4. 验证部署是否成功

### 4.1 健康检查

```bash
curl http://localhost:8000/health
```

如果返回内容中包含：

```json
{
  "status": "ok"
}
```

说明 API 服务已启动。

也可以通过 Nginx 访问：

```bash
curl http://localhost/health
```

### 4.2 打开接口文档

浏览器访问：

```text
http://localhost:8000/docs
```

如果你想走 Nginx 入口，访问：

```text
http://localhost/docs
```

Swagger 页面中可以直接点击接口的 `Try it out` 测试服务。

### 4.3 测试主对话接口

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "我的订单什么时候发货？",
    "user_id": "user_001",
    "conv_id": "session_001"
  }'
```

正常返回会包含：

```json
{
  "conv_id": "session_001",
  "response": "...",
  "intent": "...",
  "agent_type": "...",
  "escalated": false,
  "latency_ms": 1234.5
}
```

### 4.4 查看知识库状态

```bash
curl http://localhost:8000/knowledge/stats
```

如果返回 `total_chunks`，说明知识库接口可用。

### 4.5 导入演示知识库

项目内已有演示文件：

```text
data/demo_docs/sample_knowledge.json
data/demo_docs/troubleshooting.md
```

导入 JSON 演示知识库：

```bash
curl -X POST http://localhost:8000/knowledge/upload \
  -F "file=@data/demo_docs/sample_knowledge.json"
```

再查询一次统计：

```bash
curl http://localhost:8000/knowledge/stats
```

### 4.6 测试知识库检索

```bash
curl -X POST "http://localhost:8000/search?query=退款多久到账&top_k=3"
```

如果返回 `results`，说明 ChromaDB 检索链路可用。

### 4.7 查看监控

```bash
curl http://localhost:8000/monitor
```

Prometheus 页面：

```text
http://localhost:9090
```

## 5. 常用运维命令

### 5.1 查看服务

```bash
docker compose ps
```

### 5.2 查看日志

查看全部日志：

```bash
docker compose logs -f
```

只看主应用：

```bash
docker compose logs -f echomind
```

只看 ChromaDB：

```bash
docker compose logs -f chromadb
```

只看 Redis：

```bash
docker compose logs -f redis
```

### 5.3 重启服务

```bash
docker compose restart
```

只重启主应用：

```bash
docker compose restart echomind
```

### 5.4 停止服务

```bash
docker compose down
```

该命令会停止并删除容器，但不会删除数据卷和本地挂载数据。

### 5.5 重新构建镜像

代码或依赖变更后执行：

```bash
docker compose up -d --build
```

如果怀疑缓存导致镜像没有更新：

```bash
docker compose build --no-cache echomind
docker compose up -d
```

### 5.6 清空所有容器数据

谨慎执行，该命令会删除 Docker Compose 管理的数据卷：

```bash
docker compose down -v
```

如果只是想清理项目本地 ChromaDB 数据，还需要确认后再删除：

```bash
rm -rf data/chroma/*
```

## 6. 本地源码启动方式

如果你不想用 Docker 运行主应用，也可以只用 Docker 启动 Redis 和 ChromaDB，然后本机运行 Python 服务。

### 6.1 启动依赖服务

```bash
docker compose up -d redis chromadb
```

### 6.2 创建 Python 虚拟环境

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

如果本机没有 Python 3.12，可以先安装 Python 3.12。

### 6.3 安装依赖

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 6.4 确认本地 `.env`

源码启动时，本机访问 Redis 和 ChromaDB，推荐保持：

```env
REDIS_URL=redis://localhost:6379/0
CHROMA_HOST=localhost
CHROMA_PORT=8001
CHROMA_PERSIST_DIRECTORY=./data/chroma
```

如果 Redis 设置了密码，使用：

```env
REDIS_URL=redis://:echomind123@localhost:6379/0
```

### 6.5 启动 API 服务

```bash
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

验证：

```bash
curl http://localhost:8000/health
```

### 6.6 启动 CLI 交互模式

```bash
python api/main.py --cli
```

输入 `quit` 可退出。

## 7. Skills 业务能力配置

EchoMind 会从 `skills/` 目录加载业务规则，用于增强不同 Agent 的回复能力。

查看当前已加载 Skills：

```bash
curl http://localhost:8000/skills
```

修改 `skills/` 下的 `SKILL.md` 后，可以热加载：

```bash
curl -X POST http://localhost:8000/skills/reload
```

默认已有示例：

```text
skills/general_customer_service/SKILL.md
skills/technical_support/SKILL.md
skills/billing_support/SKILL.md
```

## 8. 端口说明

| 服务 | 容器名 | 宿主机端口 | 访问地址 |
|------|--------|------------|----------|
| EchoMind API | `echomind-app` | `8000` | `http://localhost:8000` |
| Swagger | `echomind-app` | `8000` | `http://localhost:8000/docs` |
| Nginx | `echomind-nginx` | `80` | `http://localhost` |
| ChromaDB | `echomind-chromadb` | `8001` | `http://localhost:8001` |
| Redis | `echomind-redis` | `6379` | 本地 Redis 客户端 |
| Prometheus | `echomind-prometheus` | `9090` | `http://localhost:9090` |

如果端口被占用，可以修改 `docker-compose.yml` 中左侧宿主机端口。例如：

```yaml
ports:
  - "18000:8000"
```

修改后访问地址变为：

```text
http://localhost:18000
```

## 9. 常见问题排查

### 9.1 `ANTHROPIC_API_KEY` 未设置

现象：

```text
RuntimeError: 未设置 ANTHROPIC_API_KEY
```

处理：

```bash
cp .env.example .env
```

然后编辑 `.env`：

```env
ANTHROPIC_API_KEY=你的真实_api_key
```

最后重启：

```bash
docker compose restart echomind
```

### 9.2 API 返回 503 服务未就绪

先看主应用日志：

```bash
docker compose logs -f echomind
```

再看依赖状态：

```bash
docker compose ps
```

常见原因：

- API Key 配置错误
- ChromaDB 没有启动成功
- Redis 没有启动成功
- 第一次启动还在下载或初始化依赖

### 9.3 `localhost:8000` 无法访问

检查容器是否运行：

```bash
docker compose ps
```

检查端口是否被占用：

```bash
lsof -i :8000
```

如果被其他程序占用，可以停止占用程序，或修改 `docker-compose.yml` 端口映射。

### 9.4 ChromaDB 健康检查失败

查看日志：

```bash
docker compose logs -f chromadb
```

检查心跳接口：

```bash
curl http://localhost:8001/api/v1/heartbeat
```

如果本地数据损坏，可以停止服务后清理 ChromaDB 数据。执行前请确认不需要保留知识库数据：

```bash
docker compose down
rm -rf data/chroma/*
docker compose up -d
```

### 9.5 Redis 连接失败

Docker Compose 模式下，主应用使用容器内地址：

```env
REDIS_URL=redis://:echomind123@redis:6379/0
```

源码本地启动时，应该使用本机地址：

```env
REDIS_URL=redis://:echomind123@localhost:6379/0
```

测试 Redis：

```bash
docker compose exec redis redis-cli -a echomind123 ping
```

正常返回：

```text
PONG
```

### 9.6 Docker 镜像构建很慢

首次构建会安装 Python 依赖，并预下载 ChromaDB 的 ONNX embedding 模型，耗时较长是正常的。

如果网络中断，重新执行：

```bash
docker compose build --no-cache echomind
docker compose up -d
```

### 9.7 Docker Compose 命令不兼容

新版本 Docker 使用：

```bash
docker compose up -d
```

老版本可能使用：

```bash
docker-compose up -d
```

本项目推荐使用新版本 `docker compose`。如果你使用项目里的 `docker-deploy.sh`，脚本内部调用的是老命令 `docker-compose`，需要本机安装对应命令，或直接使用本文档中的 `docker compose` 命令。

## 10. 最小部署命令速查

如果你已经安装好 Docker，并准备好了 API Key，可以只执行这一组命令：

```bash
cd /Users/xiao_xiong/Desktop/code/EchoMind
cp .env.example .env
```

编辑 `.env`，填入：

```env
ANTHROPIC_API_KEY=你的真实_api_key
```

启动：

```bash
docker compose up -d --build
```

验证：

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"你好，我想查询订单","user_id":"demo_user","conv_id":"demo_session"}'
```

浏览器打开：

```text
http://localhost:8000/docs
```

到这里，EchoMind 就已经完成从 0 到 1 的本地部署。
