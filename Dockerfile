# EchoMind 智能客服系统 - Docker部署配置
# 多阶段构建，优化镜像大小和构建效率

# ============================================
# 阶段1: 基础环境构建
# ============================================
FROM python:3.12-slim as base

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# ============================================
# 阶段2: 依赖安装
# ============================================
FROM base as dependencies

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# ============================================
# 阶段3: 应用构建
# ============================================
FROM dependencies as builder

# 复制应用代码
COPY . .

# 创建必要的目录
RUN mkdir -p /app/data/chroma /app/logs /app/config

# 设置权限
RUN chmod -R 755 /app

# ============================================
# 阶段4: 生产镜像
# ============================================
FROM base as production

# 从构建阶段复制依赖
COPY --from=dependencies /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=dependencies /usr/local/bin /usr/local/bin

# 复制应用代码
COPY --from=builder /app /app

# 创建非root用户
RUN useradd -m -u 1000 echomind && \
    chown -R echomind:echomind /app

# 切换到非root用户
USER echomind

# 设置工作目录
WORKDIR /app

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]

# ============================================
# 阶段5: 开发环境镜像
# ============================================
FROM base as development

# 安装开发工具
RUN pip install --upgrade pip && \
    pip install pytest pytest-asyncio pytest-cov black flake8 mypy && \
    pip install -r requirements.txt

# 复制应用代码
COPY . .

# 创建开发目录
RUN mkdir -p /app/data/chroma /app/logs /app/config /app/tests

# 设置权限
RUN chmod -R 777 /app/data /app/logs

# 暴露端口
EXPOSE 8000 5678

# 开发模式启动命令
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ============================================
# 阶段6: 测试环境镜像
# ============================================
FROM development as test

# 复制测试配置
COPY pytest.ini pyproject.toml ./

# 测试命令
CMD ["pytest", "tests/", "-v", "--cov=.", "--cov-report=html"]