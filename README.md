# 智能客服工单 Agent 系统

基于 **LangGraph + DeepSeek V4 Pro + ChromaDB** 的企业级智能客服系统，支持自然语言对话、RAG 知识库检索、工单全生命周期管理、多 Agent 协作编排。

> 📖 完整开发教程：21 步从零搭建企业级 Agent 系统

## 技术栈

| 层 | 技术 |
|------|------|
| LLM | DeepSeek V4 Pro（OpenAI 兼容 API），动态模型路由（简单用 Flash，复杂用 Pro） |
| 后端 | Python 3.12 + FastAPI + LangChain + LangGraph |
| 向量库 | ChromaDB + BM25 关键词 + RRF 融合 + Cross-Encoder 精排 |
| Embedding | paraphrase-multilingual-MiniLM-L12-v2（本地运行，免费） |
| 数据库 | SQLite（开发）/ 升级路径 PostgreSQL |
| 协议 | MCP（Model Context Protocol）—— 工具外部化，stdio 子进程通信 |
| 工作流 | 自研 WorkflowEngine 状态机 — 9 条转换规则 + guard 守卫 + SLA 自动升级 |
| 前端 | React 18 + Ant Design 5 + Vite |
| 认证 | API Key（客户端层）+ 用户登录 Token（会话层）+ 工单归属（数据层） |
| 部署 | Docker Compose（前后端 + 数据卷）+ 一键启动脚本 |

## 架构

```
用户输入 → 语义守卫(SemanticGuard) → Supervisor 意图分析 →
├── 闲聊 → ChatAgent
├── 知识库 → RAG Agent → 混合检索(BM25+语义+RRF+精排) → DeepSeek 生成
├── 工具 → Tool Agent → 调用内置工具 / MCP 工具（状态变更走 WorkflowEngine）
└── 混合 → LangGraph Orchestrator → 多 Agent 协作
```

## 项目结构（44 个源文件）

```
├── docker-compose.yml          # Docker 一键编排
├── .env.docker                  # 环境变量模板
├── 启动Docker.bat               # 双击启动
├── agent-project/               # 后端
│   ├── Dockerfile
│   ├── src/
│   │   ├── config/settings.py
│   │   ├── data/                # SQLite + 认证 + 会话记忆
│   │   ├── agents/              # ChatAgent → ToolAgent → RAGAgent + Orchestrator + Guard
│   │   ├── tools/               # 9 个内置工具（CRUD + 时间 + 计算 + FAQ）
│   │   ├── rag/                 # 文档加载 + ChromaDB + 混合检索
│   │   ├── skills/              # PersonaSkill + TimeSkill + TicketSkill
│   │   ├── mcp/                 # MCP Client + 2 Server (demo/ticket)
│   │   ├── workflow/            # 状态机引擎 + 工单生命周期（9 规则 + SLA）
│   │   ├── api/server.py       # FastAPI 入口
│   │   └── observability/       # 全链路追踪 + 仪表盘
│   └── data/documents/          # 知识库文档
└── agent-frontend/              # 前端
    ├── Dockerfile + nginx.conf
    └── src/pages/               # ChatPage + Login + Register + Tickets
```

## 📖 使用说明书（5 分钟上手）

### 你需要准备

1. **DeepSeek API Key** — 去 [platform.deepseek.com](https://platform.deepseek.com) 注册，充值 1 块钱就够用了
2. **Docker Desktop** — [下载地址](https://www.docker.com/products/docker-desktop/)，装好就行，不用学

### 第一步：获取 API Key

打开 [DeepSeek 开放平台](https://platform.deepseek.com) → 注册登录 → 左侧「API Keys」→ 创建新 Key → 复制下来（格式类似 `sk-xxxx`）。

### 第二步：配置项目

打开 `D:\MyPython code\.env` 文件（记事本就行），把第一行改成你的 Key：

```
DEEPSEEK_API_KEY=sk-你的真实Key粘贴在这里
```

### 第三步：启动

双击 `D:\MyPython code\启动Docker.bat`，等 1 分钟出现黑窗口提示完成。

或者命令行：
```bash
cd D:\MyPython code
docker compose up -d
```

### 第四步：开始使用

浏览器打开 **http://localhost:5173/chat**

#### 👤 注册账号

点右上角「登录」→「注册」→ 填用户名、邮箱、密码 → 注册成功自动登录。

#### 💬 跟 Agent 对话

直接输入问题，比如：
- `帮我开个工单，数据导出失败，优先级高`
- `忘了密码怎么办`
- `查一下所有工单`
- `现在几点了`
- `你是谁`

Agent 会自动判断意图，调工具或查知识库。

#### 📋 管理工单

点顶部「工单管理」进入工单面板——可以查看、创建、编辑状态、关闭工单。

#### 🔐 管理员功能

用管理员账号登录可以：编辑任何人的工单、物理删除。

### 停止运行

```bash
docker compose down
```

### 不用 Docker？本地跑

```bash
# 终端1：后端
cd agent-project
pip install -r requirements.txt
python src/api/server.py

# 终端2：前端
cd agent-frontend
npm install
npm run dev
```

### 常见问题

| 问题 | 解决 |
|------|------|
| 登录提示「用户名或密码错误」 | 首次使用需要先注册账号 |
| 对话没有反应 | 检查 `.env` 里的 API Key 对不对 |
| Docker 启动不了 | 确保 Docker Desktop 在右下角运行中 |
| 页面打不开 | 等 1 分钟让后端加载完 Embedding 模型 |
| 想查看 API 文档 | 打开 http://localhost:8000/docs |

## 演示账号

| 用户 | 密码 | 角色 |
|------|------|------|
| demo | demo123 | 普通用户 |
| admin | 123456 | 管理员 |

## 核心特性

- 🤖 **多 Agent 编排** — LangGraph StateGraph，Supervisor → RAG/Tool/Chat → Synthesizer
- 🔍 **混合检索 RAG** — BM25 关键词 + 语义向量 + RRF 融合 + Cross-Encoder 精排
- 📋 **工单工作流** — 状态机引擎，9 条转换规则，前置守卫 + 前后钩子
- ⏱️ **SLA 监控** — 超时自动升级优先级（低→中→高→紧急）
- 🛡️ **语义守卫** — 21 个关键词优先匹配（0ms），向量兜底覆盖变体
- 💬 **多轮记忆** — SQLite 持久化对话，前端 sid 串联上下文
- 🔐 **三层认证** — API Key（客户端）+ Token（用户）+ 工单归属（数据层）
- 🎭 **PersonaSkill** — 2536 字技能书，身份约束（不声称是 DeepSeek）
- 🔧 **MCP 协议** — 工具外部化，stdio 通信，2 个独立 Server
- 🐳 **Docker 部署** — 前后端分离构建，docker compose 一键编排

## 开发历程

本项目为 21 步完整开发教程实践：

1. 项目搭建与环境配置
2. LLM 对话 Agent
3. Tool Calling（Function Calling）
4. 文档向量化与检索
5. RAG Agent
6. 混合检索 + Reranker
7. Agent 基类 + 意图路由
8. 多 Agent 编排（LangGraph）
9. SQLite 持久化 + 用户认证
10. MCP Client 开发
11. MCP Server 开发
12. FastAPI 服务封装 + SSE 流式
13. 可观测性 + Langfuse
14. React 前端 + 对话界面
15. 前端登录/注册 + 打字机效果
16. 工单管理面板
17. 全链路联调 + 安全加固
18. 会话记忆与多轮对话
19. 工单生命周期工作流
20. Docker 一键部署
21. 一键启动脚本

## API 文档

启动后访问 http://localhost:8000/docs 查看 Swagger 文档。

主要端点：
- `POST /api/auth/register` — 注册
- `POST /api/auth/login` — 登录
- `POST /api/chat` — Agent 对话（支持 SSE 流式）
- `GET/POST /api/tickets` — 工单列表/创建
- `GET/PUT/DELETE /api/tickets/{id}` — 工单详情/更新/关闭
- `GET /api/workflow/status` — 查看工作流
- `POST /api/workflow/sla-check` — SLA 扫描

## License

MIT
