# ============================================
# 2026-06-19 - FastAPI 服务
# 职责：把 Agent 系统暴露为 RESTful API
#
# 端点：
#   POST /api/auth/register   — 注册
#   POST /api/auth/login      — 登录
#   POST /api/auth/logout     — 登出
#   GET  /api/auth/me         — 当前用户
#
#   POST /api/chat            — Agent 对话（支持流式 SSE）
#
#   GET  /api/tickets         — 工单列表
#   POST /api/tickets         — 创建工单
#   GET  /api/tickets/{id}    — 工单详情
#   PUT  /api/tickets/{id}    — 更新工单
#   DELETE /api/tickets/{id}  — 关闭工单
#
# 认证：Header 传 Authorization: Bearer <token>
# API Key：Header 传 X-API-Key: <key>
# ============================================

import sys
import os
import json
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from loguru import logger

from src.config.settings import settings
from src.data.database import init_database, get_session
from src.data.models import User, Ticket, Conversation  # Conversation = 会话记忆表
from src.data.auth import AuthService, verify_api_key
from src.workflow.ticket_workflow import execute_transition, get_ticket_workflow, scan_all_sla
from src.data.context import set_current_user, clear_current_user
from src.agents.orchestrator import MultiAgentOrchestrator
from src.agents.guard import SemanticGuard
from src.rag.vector_store import VectorStore
from src.rag.document_loader import DocumentLoader


# ============================================
# 请求/响应模型
# ============================================

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=100)

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    email: str = Field(..., max_length=120)
    password: str = Field(..., min_length=6, max_length=100)

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    stream: bool = Field(default=True)
    sid: str | None = Field(default=None)    # 可选：会话 ID，用于恢复上下文

class TicketCreateRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=200)
    priority: str = Field(default="中")
    description: str = Field(default="")

class TicketUpdateRequest(BaseModel):
    status: str | None = None
    assignee: str | None = None


# ============================================
# 应用生命周期
# ============================================

# 全局 Agent 实例
_orchestrator: MultiAgentOrchestrator | None = None


def _seed_database():
    """种子数据：确保演示用户和示例工单存在"""
    from src.data.database import get_session as gs
    from src.data.models import User
    from src.data.auth import hash_password

    with gs() as db:
        # 创建 demo 用户
        if not db.query(User).filter(User.username == "demo").first():
            db.add(User(
                username="demo", email="demo@example.com",
                password_hash=hash_password("demo123"),
                display_name="演示用户", role="user",
            ))
            logger.info("种子用户: demo / demo123")

        # 创建 admin 用户
        if not db.query(User).filter(User.username == "admin").first():
            db.add(User(
                username="admin", email="admin@example.com",
                password_hash=hash_password("123456"),
                display_name="管理员", role="admin",
            ))
            logger.info("种子用户: admin / 123456")

        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭"""
    global _orchestrator

    logger.info("正在启动 Agent 服务...")

    # 初始化数据库
    init_database()

    # 种子数据：确保 demo/admin 用户和示例工单存在（Docker 首次启动用）
    _seed_database()

    # 确保知识库有数据
    docs_dir = os.path.join(settings.PROJECT_ROOT, "data", "documents")
    vs = VectorStore(persist_dir=os.path.join(settings.PROJECT_ROOT, "data", "chroma_db"))
    if vs.doc_count == 0:
        loader = DocumentLoader()
        chunks = loader.load_directory(docs_dir)
        vs.add_documents(chunks)
        logger.info(f"知识库已导入: {vs.doc_count} 块")

    # 创建编排器
    _orchestrator = MultiAgentOrchestrator()

    logger.info(f"Agent 服务就绪，端口: {settings.API_PORT}")
    yield
    logger.info("Agent 服务关闭")


# 创建 FastAPI 应用
# swagger_ui_js_url → 使用国内 CDN，解决 /docs 页面加载不了的问题
app = FastAPI(
    title="智能客服工单 Agent 系统",
    description="基于 LangGraph 的企业级 Agent 系统 API",
    version="1.0.0",
    lifespan=lifespan,
    swagger_ui_parameters={"tryItOutEnabled": True},
)

# CORS（credentials=True 时不能用 * 通配符）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================
# 依赖注入：认证
# ============================================

def check_api_key(x_api_key: str | None = Header(None)):
    """验证 API Key"""
    if not verify_api_key(x_api_key):
        raise HTTPException(403, "Invalid or missing API Key")
    return x_api_key


def get_user_optional(authorization: str | None = Header(None)) -> dict | None:
    """获取当前用户（允许未登录）"""
    if not authorization:
        return None
    token = authorization.replace("Bearer ", "")
    user = AuthService.authenticate(token)
    if user:
        set_current_user(user)
        return user
    set_current_user(None)
    return None


def require_user(authorization: str | None = Header(None)) -> dict:
    """获取当前用户（必须登录）"""
    user = get_user_optional(authorization)
    if not user:
        raise HTTPException(401, "需要登录")
    return user


# ============================================
# 认证 API
# ============================================

@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    """注册"""
    result = AuthService.register(req.username, req.email, req.password)
    if not result["success"]:
        raise HTTPException(400, result["message"])
    return result


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    """登录"""
    result = AuthService.login(req.username, req.password)
    if not result["success"]:
        raise HTTPException(401, result["message"])
    return result


@app.post("/api/auth/logout")
async def logout(authorization: str | None = Header(None)):
    """登出"""
    if authorization:
        token = authorization.replace("Bearer ", "")
        AuthService.logout(token)
    return {"success": True, "message": "已登出"}


@app.get("/api/auth/me")
async def me(user: dict = Depends(require_user)):
    """当前用户信息"""
    return {"user": user}


# ============================================
# 对话 API（核心）
# ============================================

@app.post("/api/chat")
async def chat(
    req: ChatRequest,
    user: dict | None = Depends(get_user_optional),
    _api_key: str = Depends(check_api_key),
):
    """Agent 对话（支持 SSE 流式）"""
    if not _orchestrator:
        raise HTTPException(503, "服务未就绪")

    # 语义守卫
    is_guarded = not user and _orchestrator._guard.check(req.message)
    if is_guarded:
        # 守卫拦截 → 包装为 SSE 流（前端 stream=true 时也能解析）
        if req.stream:
            return StreamingResponse(
                _guard_stream("请登录"),
                media_type="text/event-stream",
            )
        return {"reply": "请登录", "intent": "blocked", "trace": ["guard"]}

    set_current_user(user)
    uid = user.get("user_id", user.get("id")) if user else None
    sid = req.sid or _orchestrator.memory.new(uid, req.message[:30])
    _orchestrator._active_sid = sid

    if req.stream:
        return StreamingResponse(
            _stream_chat(req.message, user),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )
    else:
        result = await asyncio.to_thread(_orchestrator.run, req.message)
        return {
            "reply": result["final_response"],
            "intent": result["intent"],
            "trace": result["trace"],
            "sid": sid,
        }


async def _guard_stream(msg: str):
    """守卫拦截提示的 SSE 流 — 让前端 stream=true 时也能正常解析"""
    import asyncio
    yield f"data: {json.dumps({'type': 'meta', 'intent': 'blocked', 'trace': ['guard']}, ensure_ascii=False)}\n\n"
    # 逐字推送
    for i in range(0, len(msg), 2):
        chunk = msg[i:i+2]
        yield f"data: {json.dumps({'type': 'token', 'content': chunk}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.03)
    yield "data: [DONE]\n\n"


async def _stream_chat(message: str, user: dict | None = None):
    """SSE 流式推送（传入 user 确保跨线程可用）"""
    import asyncio
    try:
        # 在线程前设置用户上下文（asyncio.to_thread 不传播 contextvars）
        def _run_with_user():
            set_current_user(user)
            return _orchestrator.run(message)
        result = await asyncio.to_thread(_run_with_user)

        reply = result["final_response"]

        # 先发元数据
        yield f"data: {json.dumps({'type': 'meta', 'intent': result['intent'], 'trace': result['trace']}, ensure_ascii=False)}\n\n"

        if result["tool_results"]:
            yield f"data: {json.dumps({'type': 'tool_results', 'data': result['tool_results']}, ensure_ascii=False)}\n\n"

        # 模拟逐字推送（真实流式需要改 orche 底层，这里用分句模拟）
        for i in range(0, len(reply), 2):
            chunk = reply[i:i+2]
            yield f"data: {json.dumps({'type': 'token', 'content': chunk}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.03)  # 30ms 间隔，模拟打字效果

        yield "data: [DONE]\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"


# ============================================
# 工单 API
# ============================================

@app.get("/api/tickets")
async def list_tickets_api(
    status: str | None = None,
    user: dict = Depends(require_user),
    _api_key: str = Depends(check_api_key),
):
    """工单列表"""
    from src.data.database import get_session as gs
    from src.data.models import Ticket as TM

    with gs() as db:
        query = db.query(TM).order_by(TM.created_at.desc())
        if status:
            query = query.filter(TM.status == status)
        tickets = query.limit(50).all()
        return {"tickets": [t.to_dict() for t in tickets]}


@app.post("/api/tickets")
async def create_ticket_api(
    req: TicketCreateRequest,
    user: dict = Depends(require_user),
    _api_key: str = Depends(check_api_key),
):
    """创建工单"""
    import uuid
    from src.data.database import get_session as gs

    if req.priority not in ("紧急", "高", "中", "低"):
        req.priority = "中"

    sla = {"紧急": 4, "高": 24, "中": 72, "低": 168}
    ticket_no = f"TK{uuid.uuid4().hex[:4].upper()}"

    with gs() as db:
        t = Ticket(
            ticket_no=ticket_no,
            title=req.title,
            priority=req.priority,
            status="待处理",
            description=req.description,
            sla_hours=sla[req.priority],
            user_id=user["user_id"],
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return {"ticket": t.to_dict()}


@app.get("/api/tickets/{ticket_no}")
async def get_ticket(
    ticket_no: str,
    user: dict = Depends(require_user),
    _api_key: str = Depends(check_api_key),
):
    """工单详情"""
    from src.data.database import get_session as gs

    with gs() as db:
        t = db.query(Ticket).filter(Ticket.ticket_no == ticket_no.upper()).first()
        if not t:
            raise HTTPException(404, f"未找到工单 {ticket_no}")
        return {"ticket": t.to_dict()}


@app.put("/api/tickets/{ticket_no}")
async def update_ticket_api(
    ticket_no: str,
    req: TicketUpdateRequest,
    user: dict = Depends(require_user),
    _api_key: str = Depends(check_api_key),
):
    """更新工单（仅管理员，走工作流状态机）"""
    from src.data.database import get_session as gs

    if user.get("role") != "admin":
        raise HTTPException(403, "只有管理员才能编辑工单")

    with gs() as db:
        t = db.query(Ticket).filter(Ticket.ticket_no == ticket_no.upper()).first()
        if not t:
            raise HTTPException(404, f"未找到工单 {ticket_no}")

        # 状态变更：走工作流
        if req.status and req.status != t.status:
            ok, msg = execute_transition(
                ticket=t,
                to_status=req.status,
                operator=user,
                db_session=db,
                assignee_name=req.assignee or t.assignee,
            )
            if not ok:
                raise HTTPException(400, msg)

        if req.assignee:
            t.assignee = req.assignee
        t.updated_at = datetime.now()
        db.commit()
        return {"ticket": t.to_dict()}


@app.delete("/api/tickets/{ticket_no}")
async def close_ticket(
    ticket_no: str,
    user: dict = Depends(require_user),
    hard: bool = False,
    _api_key: str = Depends(check_api_key),
):
    """关闭工单（走工作流）/ 硬删除仅管理员"""
    from src.data.database import get_session as gs

    with gs() as db:
        t = db.query(Ticket).filter(Ticket.ticket_no == ticket_no.upper()).first()
        if not t:
            raise HTTPException(404, f"未找到工单 {ticket_no}")
        is_admin = user.get("role") == "admin"
        if hard:
            if not is_admin:
                raise HTTPException(403, "只有管理员才能彻底删除工单")
            db.delete(t)
            db.commit()
            return {"deleted": True, "ticket_no": ticket_no}

        # 软关闭：走工作流
        ok, msg = execute_transition(
            ticket=t,
            to_status="已关闭",
            operator=user,
            db_session=db,
            reason=user.get("display_name", "?") + " 关闭工单",
        )
        if not ok:
            raise HTTPException(400, msg)

        t.updated_at = datetime.now()
        db.commit()
        return {"ticket": t.to_dict()}


# ============================================
# 会话记忆 API
# ============================================

@app.get("/api/conversations")
async def list_conversations(user: dict | None = Depends(get_user_optional)):
    """获取用户的会话历史列表"""
    if not _orchestrator:
        raise HTTPException(503, "服务未就绪")
    uid = user["user_id"] if user else None
    return {"conversations": _orchestrator.memory.get_all(uid)}


@app.get("/api/conversations/{sid}")
async def get_conversation(sid: str):
    """加载某个会话的完整对话（恢复上下文）"""
    if not _orchestrator:
        raise HTTPException(503, "服务未就绪")
    msgs = _orchestrator.memory.load(sid)
    if msgs is None:
        raise HTTPException(404, "会话不存在或已过期")
    return {"sid": sid, "messages": msgs}


# ============================================
# 健康检查
# ============================================

@app.get("/api/health")
async def health():
    wf = get_ticket_workflow()
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "agent_ready": _orchestrator is not None,
        "workflow": {
            "name": wf.name,
            "transitions": len(wf.allowed_transitions),
            "states": sorted(set(t["from"] for t in wf.allowed_transitions) | set(t["to"] for t in wf.allowed_transitions)),
        },
    }


@app.get("/api/workflow/status")
async def workflow_status():
    """查看工作流定义和 SLA 状态"""
    wf = get_ticket_workflow()
    return {
        "name": wf.name,
        "allowed_transitions": wf.allowed_transitions,
    }


@app.post("/api/workflow/sla-check")
async def sla_check(_api_key: str = Depends(check_api_key)):
    """手动触发全量 SLA 扫描"""
    from src.data.database import get_session as gs
    with gs() as db:
        alerts = scan_all_sla(db)
    return {"alerts": alerts, "count": len(alerts)}


# ============================================
# 启动入口
# ============================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.API_PORT, log_level="info")