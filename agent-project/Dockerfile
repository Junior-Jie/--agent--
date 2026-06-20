# ============================================
# 智能客服工单 Agent 系统 — 后端 Dockerfile
# Python 3.12 + FastAPI + ChromaDB + DeepSeek
# ============================================

FROM docker.m.daocloud.io/library/python:3.12-slim

LABEL maintainer="agent-project"
LABEL description="智能客服工单 Agent 系统 - 后端服务"

# 设置国内镜像 / 编码
ENV HF_ENDPOINT=https://hf-mirror.com
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
ENV LANG=C.UTF-8

WORKDIR /app

# 先装系统依赖（轻量）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件（利用 Docker 缓存层）
COPY requirements.txt .

# 安装 pip 依赖（用阿里云镜像加速）
RUN pip install --no-cache-dir \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    -r requirements.txt

# 复制源码
COPY . .

# 创建数据和日志目录
RUN mkdir -p /app/data/documents /app/data/chroma_db /app/logs

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f -H "X-API-Key: ${APP_API_KEY:-changeme}" http://localhost:8000/api/health || exit 1

# 启动
CMD ["python", "src/api/server.py"]
