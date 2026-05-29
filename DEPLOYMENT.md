# EchoMind 快速部署指南

## 📦 部署方式选择

### 方式一：Docker Compose（推荐新手）

**适用场景**：快速部署、开发环境、测试环境

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，设置 ANTHROPIC_API_KEY

# 2. 一键部署
./docker-deploy.sh install
./docker-deploy.sh start

# 3. 查看状态
./docker-deploy.sh status

# 4. 查看日志
./docker-deploy.sh logs
```

**优点**：
- 最简单，一键部署
- 自动处理所有依赖
- 包含完整的服务编排

---

### 方式二：单独构建镜像（推荐生产环境）

**适用场景**：生产环境、镜像分发、CI/CD集成

#### 2.1 构建镜像

```bash
# 构建生产镜像
./build-image.sh build-prod

# 或禁用缓存构建
./build-image.sh build-prod --no-cache

# 多平台构建
./build-image.sh build-prod --platform linux/amd64,linux/arm64
```

#### 2.2 运行镜像

```bash
# 运行容器
./run-image.sh run --detach

# 开发模式运行
./run-image.sh run-dev --detach

# 使用自定义环境文件
./run-image.sh run --env-file .env.prod --detach
```

#### 2.3 推送到镜像仓库

```bash
# 设置仓库地址
./build-image.sh push --registry your-registry.com

# 或推送到 Docker Hub
docker tag echomind:latest username/echomind:latest
docker push username/echomind:latest
```

---

### 方式三：传统 Docker 命令（适合熟悉 Docker 的用户）

#### 3.1 构建镜像

```bash
# 生产镜像
docker build -t echomind:latest --target production .

# 开发镜像
docker build -t echomind:dev --target development .
```

#### 3.2 运行容器

```bash
# 基础运行
docker run -d \
  --name echomind \
  -p 8000:8000 \
  --env-file .env \
  echomind:latest

# 完整配置运行
docker run -d \
  --name echomind \
  --restart unless-stopped \
  -p 8000:8000 \
  -p 9090:9090 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/config:/app/config \
  --env-file .env \
  echomind:latest
```

---

## 🔧 环境要求

### 必需
- Docker 20.10+
- Docker Compose 2.0+
- 4GB+ 可用内存

### 可选
- Redis 服务（或使用 Docker Compose 自动启动）
- ChromaDB 服务（或使用 Docker Compose 自动启动）

---

## ⚙️ 环境配置

### 1. 复制环境变量模板

```bash
cp .env.example .env
```

### 2. 必需配置项

```bash
# Anthropic API 密钥（必需）
ANTHROPIC_API_KEY=your_api_key_here

# Redis 配置
REDIS_URL=redis://localhost:6379/0

# ChromaDB 配置
CHROMA_HOST=localhost
CHROMA_PORT=8001
```

### 3. 可选配置项

```bash
# 模型选择
CLAUDE_MODEL=claude-3-5-sonnet-20240229

# 性能调优
WORKERS=4
MAX_CONNECTIONS=1000

# 监控配置
PROMETHEUS_ENABLED=true
GRAFANA_ENABLED=true
```

---

## 🚀 部署流程

### 快速部署（5分钟）

```bash
# 1. 克隆项目
git clone https://github.com/your-repo/echomind.git
cd echomind

# 2. 配置环境
cp .env.example .env
# 编辑 .env 文件

# 3. 启动服务
./docker-deploy.sh install
./docker-deploy.sh start

# 4. 验证部署
curl http://localhost:8000/health
```

### 生产部署

```bash
# 1. 构建镜像
./build-image.sh build-prod

# 2. 测试镜像
./run-image.sh run --detach

# 3. 验证功能
curl http://localhost:8000/health

# 4. 推送到镜像仓库
./build-image.sh push --registry your-registry.com

# 5. 在生产环境拉取运行
docker pull your-registry.com/echomind:latest
./run-image.sh run --env-file .env.prod --detach
```

---

## 📊 服务访问

部署完成后，可以访问以下服务：

| 服务 | 地址 | 说明 |
|------|------|------|
| EchoMind API | http://localhost:8000 | 主应用 API |
| API 文档 | http://localhost:8000/docs | Swagger UI |
| Prometheus | http://localhost:9090 | 监控指标 |
| Grafana | http://localhost:3000 | 监控面板 |

---

## 🛠️ 常用命令

### 服务管理

```bash
# 启动服务
./docker-deploy.sh start

# 停止服务
./docker-deploy.sh stop

# 重启服务
./docker-deploy.sh restart

# 查看状态
./docker-deploy.sh status

# 查看日志
./docker-deploy.sh logs
./docker-deploy.sh logs echomind
```

### 镜像管理

```bash
# 构建镜像
./build-image.sh build-prod

# 清理缓存
./build-image.sh clean

# 添加标签
./build-image.sh tag --version v1.0.0
```

### 容器管理

```bash
# 运行容器
./run-image.sh run --detach

# 查看日志
./run-image.sh logs

# 进入容器
./run-image.sh shell

# 查看状态
./run-image.sh status

# 停止容器
./run-image.sh stop

# 清理容器
./run-image.sh clean
```

---

## 🔍 健康检查

```bash
# 应用健康检查
curl http://localhost:8000/health

# Prometheus 健康检查
curl http://localhost:9090/-/healthy

# 执行全面健康检查
./docker-deploy.sh health
```

---

## 📈 监控和日志

### 查看日志

```bash
# 所有服务日志
./docker-deploy.sh logs

# 特定服务日志
./docker-deploy.sh logs echomind
./docker-deploy.sh logs redis

# 实时跟踪日志
./docker-deploy.sh logs -f
```

### 访问监控面板

1. **Grafana**: http://localhost:3000
   - 默认账号: `admin/admin`
   - 配置 Prometheus 数据源

2. **Prometheus**: http://localhost:9090
   - 查询指标和告警规则

---

## 🚨 故障排查

### 容器无法启动

```bash
# 查看详细日志
./docker-deploy.sh logs echomind

# 检查容器状态
docker ps -a

# 进入容器检查
./run-image.sh shell
```

### 端口冲突

```bash
# 修改 .env 中的端口配置
API_PORT=8001
PROMETHEUS_PORT=9091

# 或在运行时指定端口
./run-image.sh run --ports "8001:8000,9091:9090"
```

### 依赖服务问题

```bash
# 检查 Redis 连接
docker-compose exec redis redis-cli ping

# 检查 ChromaDB 连接
curl http://localhost:8001/api/v1/heartbeat

# 重启依赖服务
docker-compose restart redis chromadb
```

---

## 🔄 更新和升级

### 更新镜像

```bash
# 1. 拉取最新代码
git pull origin main

# 2. 重新构建镜像
./build-image.sh build-prod --no-cache

# 3. 重启服务
./docker-deploy.sh restart
```

### 数据备份

```bash
# 备份数据
./docker-deploy.sh backup

# 恢复数据
./docker-deploy.sh restore backups/20231201_120000
```

---

## 🔒 安全建议

1. **修改默认密码**
   - Redis 密码
   - Grafana 密码
   - 应用密钥

2. **配置 HTTPS**
   - 使用 Nginx 反向代理
   - 配置 SSL 证书

3. **限制网络访问**
   - 使用防火墙规则
   - 配置 Docker 网络

4. **定期更新**
   - 及时更新依赖版本
   - 关注安全公告

---

## 📞 技术支持

- 文档: [README.md](README.md)
- 问题报告: GitHub Issues
- 技术讨论: GitHub Discussions

---

**祝你部署顺利！** 🎉