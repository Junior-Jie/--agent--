# ============================================
# 智能客服工单 Agent 系统 — 前端 Dockerfile
# Node 20 构建 + Nginx 静态服务
# ============================================

# ---- Stage 1: 构建 ----
FROM docker.m.daocloud.io/library/node:20-alpine AS builder

LABEL maintainer="agent-project"

WORKDIR /app

# npm 镜像加速
RUN npm config set registry https://registry.npmmirror.com

# 先复制依赖描述文件（利用 Docker 缓存）
COPY package.json package-lock.json* ./

# 安装依赖
RUN npm install

# 复制源码
COPY . .

# 生产构建
RUN npm run build

# ---- Stage 2: Nginx 运行时 ----
FROM docker.m.daocloud.io/library/nginx:alpine

# curl for healthcheck
RUN apk add --no-cache curl

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD curl -f http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
