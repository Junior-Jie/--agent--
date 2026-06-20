# 智能客服工单 Agent

一个用 LangGraph + DeepSeek 搭的工单客服系统，前后端分离，Docker 一键跑。

边学边写的东西，不是什么商业项目，但该有的都有了——对话、RAG 检索、工单管理、工作流、多轮记忆。

## 用到的东西

- 后端 Python 3.12 + FastAPI
- LLM DeepSeek V4 Pro（调 API，不本地跑）
- 编排 LangGraph StateGraph
- 向量 ChromaDB + BM25 + RRF 精排
- 前端 React 18 + Ant Design 5
- 数据库 SQLite（单机够用）
- 部署 Docker Compose

## 跑起来

### 1. 准备两样东西

- Docker Desktop，装完不用管它
- DeepSeek API Key，去 platform.deepseek.com 注册就能拿

### 2. 填 Key

打开 `.env`，把第一行 `DEEPSEEK_API_KEY=` 后面换成你自己的 Key。

### 3. 启动

双击 `启动Docker.bat`，等个一分钟，浏览器打开 http://localhost:5173/chat

### 4. 注册个账号

进页面后点右上角登录 → 先注册一个账号，然后就能用了。

### 怎么玩

- 跟它聊天：「帮我开个工单，数据导出问题，优先级高」
- 问 FAQ：「忘记密码怎么办」
- 查工单：「有哪些工单」「查一下 TK0001」
- 随便聊：「你是谁」「现在几点」

工单面板在顶部导航栏，可以增删改查。admin 账号能编辑别人的工单，普通用户只能管自己的。

### 账号

| 账号 | 密码 | 权限 |
|------|------|------|
| demo | demo123 | 普通用户 |
| admin | 123456 | 管理员 |

### 不想用 Docker

```bash
# 后端
cd agent-project
pip install -r requirements.txt
python src/api/server.py

# 前端（另开一个终端）
cd agent-frontend
npm install
npm run dev
```

### 常见翻车

| 现象 | 原因 |
|------|------|
| 登录失败 | 新系统里没有用户，先注册 |
| 发了消息没反应 | .env 里的 API Key 没填或填错了 |
| 页面白屏 | 后端还在加载模型，等一分钟刷新 |
| Docker 跑不起来 | Docker Desktop 没打开 |

## 文件结构

```
├── agent-project/          # 后端
│   └── src/
│       ├── api/            # FastAPI 入口
│       ├── agents/         # ChatAgent, ToolAgent, RAGAgent, Orchestrator
│       ├── tools/          # 9 个内置工具
│       ├── rag/            # 文档加载 + 向量检索 + 混合搜索
│       ├── workflow/       # 状态机引擎 + 工单生命周期
│       ├── skills/         # PersonaSkill, TimeSkill, TicketSkill
│       ├── mcp/            # MCP 客户端 + 2 个服务端
│       ├── data/           # 数据库 + 认证 + 会话记忆
│       └── observability/  # 追踪 + 仪表盘
├── agent-frontend/         # 前端
│   └── src/pages/          # 对话页, 登录, 注册, 工单面板
├── docker-compose.yml
└── .env.docker
```

## 写这个项目的记录

21 个步骤从头搭的，大概涵盖了这些内容：

项目搭建 → 对话 Agent → 工具调用 → 文档向量化 → RAG → 混合检索 → 意图路由 → 多 Agent 编排 → 数据库 + 认证 → MCP 协议 → FastAPI 封装 → 可观测性 → 前端页面 → 登录注册 → 工单面板 → 安全加固 → 会话记忆 → 工单工作流 → Docker 部署 → 启动脚本

过程中踩了不少坑：Windows 编码问题、CORS 配置、ChromaDB 默认距离函数、threading.local 跨线程、前端消息数组下标、Docker 镜像源，都记在项目总结文档里了。

## API

后端跑起来后打开 http://localhost:8000/docs 看接口文档。

主要就这些：

- `POST /api/auth/register` 注册
- `POST /api/auth/login` 登录
- `POST /api/chat` 对话
- `GET/POST /api/tickets` 工单列表和创建
- `PUT/DELETE /api/tickets/{id}` 编辑和关闭工单

---

# Smart Ticket Agent

A ticket support system built with LangGraph + DeepSeek. Frontend + backend, one-click Docker launch.

Written while learning agent dev. Not a commercial product, but covers the basics — chat, RAG search, ticket management, workflow, multi-turn memory.

## Stack

- Backend: Python 3.12 + FastAPI
- LLM: DeepSeek V4 Pro (API calls, nothing running locally)
- Orchestration: LangGraph StateGraph
- Vector: ChromaDB + BM25 + RRF reranking
- Frontend: React 18 + Ant Design 5
- Database: SQLite (fine for single-machine)
- Deploy: Docker Compose

## Get it running

### 1. Two things to prep

- Docker Desktop. Install and forget.
- A DeepSeek API key. Sign up at [platform.deepseek.com](https://platform.deepseek.com) and grab one.

### 2. Set your key

Open `.env`, paste your key after `DEEPSEEK_API_KEY=`.

### 3. Launch

Double-click `启动Docker.bat`, wait about a minute, open http://localhost:5173/chat

### 4. Sign up

Click top-right `登录` → `注册` to create an account, then you're good to go.

### What you can do

- Chat: "帮我开个工单，数据导出问题，优先级高"
- FAQ: "忘记密码怎么办"
- Check tickets: "有哪些工单" "查一下 TK0001"
- Casual: "你是谁" "现在几点"

The ticket panel is in the top nav. Admins can edit anyone's tickets; regular users only their own.

### Accounts

| user | password | role |
|------|----------|------|
| demo | demo123 | user |
| admin | 123456 | admin |

### Without Docker

```bash
# Backend
cd agent-project
pip install -r requirements.txt
python src/api/server.py

# Frontend (a second terminal)
cd agent-frontend
npm install
npm run dev
```

### Common issues

| symptom | cause |
|---------|-------|
| login failed | no users yet in a fresh system, sign up first |
| no response | the API key in `.env` is missing or wrong |
| blank page | backend is loading the embedding model, wait a minute |
| Docker won't launch | Docker Desktop isn't running |

## Files

```
├── agent-project/          # backend
│   └── src/
│       ├── api/            # FastAPI entry
│       ├── agents/         # ChatAgent, ToolAgent, RAGAgent, Orchestrator
│       ├── tools/          # 9 built-in tools
│       ├── rag/            # doc loading + vector search + hybrid retrieval
│       ├── workflow/       # state machine + ticket lifecycle
│       ├── skills/         # PersonaSkill, TimeSkill, TicketSkill
│       ├── mcp/            # MCP client + 2 servers
│       ├── data/           # database + auth + conversation memory
│       └── observability/  # tracing + dashboard
├── agent-frontend/         # frontend
│   └── src/pages/          # chat, login, register, tickets
├── docker-compose.yml
└── .env.docker
```

## How this was built

21 steps from scratch, roughly covering:

Project setup → Chat agent → Tool calling → Doc vectorization → RAG → Hybrid search → Intent routing → Multi-agent orchestration → Database + auth → MCP → FastAPI → Observability → Frontend → Login/register → Ticket panel → Security hardening → Conversation memory → Ticket workflow → Docker deploy → Launch script

Hit plenty of bumps along the way — Windows encoding, CORS config, ChromaDB default distance, threading.local across threads, frontend array indexing, Docker registries — all noted in the project summary doc.

## API

Once the backend is up, check http://localhost:8000/docs for the full spec.

Main endpoints:

- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/chat`
- `GET/POST /api/tickets`
- `PUT/DELETE /api/tickets/{id}`
