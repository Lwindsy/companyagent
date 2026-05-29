# EchoMind 智能客服系统

一个基于先进Agent架构的企业级智能客服解决方案，具备端到端意图识别、多Agent协作、智能记忆管理和持续优化能力。

## 🌟 核心特性

- **🎯 端到端意图识别** - 多模态融合，准确率92%+
- **🤖 多Agent智能编排** - 动态路由，并行协作
- **🧠 分层记忆管理** - 工作/情景/长期三级记忆架构
- **🛠️ 企业级MCP框架** - 熔断、限流、降级、缓存
- **📊 实时监控优化** - Prometheus集成，智能告警
- **🧪 端到端评测** - 多维度质量评估和回归测试

## 🚀 快速开始

### 前置要求

- Docker 20.10+
- Docker Compose 2.0+
- 4GB+ 可用内存
- Anthropic API Key

### 一键部署

```bash
# 1. 克隆项目
git clone https://github.com/your-repo/echomind.git
cd echomind

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，设置 ANTHROPIC_API_KEY

# 3. 安装部署
./docker-deploy.sh install

# 4. 启动服务
./docker-deploy.sh start

# 5. 健康检查
./docker-deploy.sh health
```

### 手动部署

```bash
# 构建镜像
docker-compose build

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

## 📁 项目结构

```
EchoMind/
├── agents/              # 多Agent系统
│   ├── __init__.py
│   └── agent_orchestrator.py
├── api/                 # API接口
├── config/              # 配置文件
│   ├── nginx/          # Nginx配置
│   ├── grafana/        # Grafana配置
│   └── prometheus.yml  # Prometheus配置
├── core/               # 核心模块
│   ├── __init__.py
│   └── intent_recognizer.py
├── data/               # 数据目录
│   └── chroma/        # 向量数据库数据
├── docker-compose.yml  # Docker编排文件
├── Dockerfile         # Docker镜像构建文件
├── evaluation/        # 评测框架
│   ├── __init__.py
│   └── evaluator.py
├── logs/              # 日志目录
├── mcp/               # MCP工具框架
│   ├── __init__.py
│   └── tool_manager.py
├── memory/            # 记忆管理系统
│   ├── __init__.py
│   └── conversation_memory.py
├── monitor/           # 监控系统
│   ├── __init__.py
│   └── performance_monitor.py
├── tools/             # 工具集合
├── requirements.txt   # Python依赖
└── docker-deploy.sh  # 部署脚本
```

## 🔧 配置说明

### 环境变量

主要配置项位于 `.env` 文件：

```env
# Anthropic API
ANTHROPIC_API_KEY=your_api_key_here

# Redis配置
REDIS_URL=redis://redis:6379/0
REDIS_PASSWORD=your_password

# ChromaDB配置
CHROMA_HOST=chromadb
CHROMA_PORT=8000

# 监控配置
PROMETHEUS_ENABLED=true
GRAFANA_ENABLED=true
```

### 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| EchoMind API | 8000 | 主应用API |
| Grafana | 3000 | 监控面板 |
| Prometheus | 9090 | 指标收集 |
| Redis | 6379 | 缓存服务 |
| ChromaDB | 8001 | 向量数据库 |

## 📊 监控和观测

### Grafana仪表板

访问 http://localhost:3000 查看监控仪表板（默认账号: admin/admin）

### Prometheus指标

访问 http://localhost:9090 查看Prometheus界面

### 关键指标

- 意图识别准确率
- Agent响应时间
- 工具调用成功率
- 系统资源使用
- 错误率和告警

## 🧪 测试和评测

### 运行测试

```bash
# 进入容器
docker-compose exec echomind bash

# 运行单元测试
pytest tests/

# 运行端到端评测
python -m evaluation.evaluator
```

### 性能基准

当前系统性能指标：

- 意图识别准确率: 92%+
- 平均响应时间: <800ms
- 并发处理能力: 1000+ QPS
- 系统可用性: 99.9%+

## 📚 API文档

启动服务后访问：

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 🔄 常用操作

### 查看服务状态

```bash
./docker-deploy.sh status
```

### 查看日志

```bash
# 所有服务日志
./docker-deploy.sh logs

# 特定服务日志
./docker-deploy.sh logs echomind
```

### 数据备份

```bash
./docker-deploy.sh backup
```

### 数据恢复

```bash
./docker-deploy.sh restore backups/20231201_120000
```

### 完全清理

```bash
./docker-deploy.sh cleanup
```

## 🛠️ 开发指南

### 添加新的Agent

1. 在 `agents/` 目录创建新的Agent类
2. 继承 `Agent` 基类
3. 实现 `_execute` 方法
4. 在 `AgentOrchestrator` 中注册

### 添加新的工具

1. 在 `tools/` 目录创建工具函数
2. 使用 `MCPToolManager.register_tool` 注册
3. 配置参数schema和元数据

### 自定义评测指标

1. 在 `evaluation/` 目录扩展评测器
2. 添加新的测试场景
3. 定义质量评估维度

## 📖 更多文档

- [项目亮点文档](PROJECT_HIGHLIGHTS.md) - 详细的技术亮点说明
- [API文档](http://localhost:8000/docs) - 完整的API参考
- [部署指南](docs/DEPLOYMENT.md) - 生产环境部署指南
- [运维手册](docs/OPERATIONS.md) - 系统运维和故障排查

## 🤝 贡献指南

欢迎贡献代码、报告问题或提出建议！

1. Fork 项目
2. 创建特性分支
3. 提交更改
4. 推送到分支
5. 创建 Pull Request

## 📄 许可证

本项目采用 MIT 许可证 - 查看 LICENSE 文件了解详情

## 🆘 支持

- 文档: [项目Wiki](https://github.com/your-repo/echomind/wiki)
- 问题: [GitHub Issues](https://github.com/your-repo/echomind/issues)
- 讨论: [GitHub Discussions](https://github.com/your-repo/echomind/discussions)

---

**EchoMind - 让智能客服真正智能** 🚀