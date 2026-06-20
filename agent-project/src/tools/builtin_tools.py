# ============================================
# 2026-06-19 - 内置工具集（SQLite 持久化版）
# 职责：所有 Agent 可调用的工具函数
# 包含：时间、计算、工单 CRUD（SQLite）、FAQ 搜索
#
# 权限设计：
#   · 公开工具（无需登录）：时间、计算、FAQ 搜索、浏览工单列表
#   · 登录工具（需要认证）：创建工单、更新工单、删除工单
#   · 工单归属：创建后自动绑定当前登录用户
# ============================================

import uuid
from datetime import datetime
from loguru import logger

from src.tools.base import Tool, ToolRegistry
from src.data.context import get_current_user, is_authenticated
from src.data.database import get_session, init_database
from src.data.models import Ticket, User
from src.workflow.ticket_workflow import execute_transition, get_ticket_workflow, check_sla_escalation


# ============================================
# 公开工具（无需登录）
# ============================================

def get_current_time() -> str:
    """获取当前日期和时间"""
    now = datetime.now()
    return now.strftime("%Y年%m月%d日 %H:%M:%S 星期%w")


def calculate(expression: str) -> str:
    """安全的数学计算器"""
    allowed_chars = set("0123456789+-*/().%^ ")
    if not all(c in allowed_chars for c in expression):
        return f"错误：表达式包含不允许的字符"
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算错误: {e}"


def search_faq(query: str) -> str:
    """搜索常见问题 FAQ"""
    faq_db = {
        "密码": "重置密码：1) 登录页点'忘记密码' 2) 输入注册邮箱 3) 查收重置链接(30分钟有效) 4) 设置新密码(不能与近5次相同)",
        "浏览器": "推荐 Chrome 100+、Edge 100+、Firefox 90+。不支持 IE。",
        "数据导出": "1) 进入报表中心 2) 选择报表类型和日期 3) 点'导出Excel'。超10000行异步发到邮箱。",
        "登录": "登录失败排查：1) 账号密码是否正确 2) 网络 3) 是否被锁定(输错5次锁30分钟) 4) 是否启用SSO",
        "权限": "权限申请：系统设置→权限管理→申请权限→选角色→填理由→部门审批(1-2工作日)",
        "API": "平台提供 RESTful API，API Key 认证。文档：https://cloud.example.com/api/docs",
    }
    for keyword, answer in faq_db.items():
        if keyword in query:
            return f"【{keyword}】{answer}"
    return f"FAQ 未收录「{query}」。建议使用知识库检索获取更全面信息。"


# ============================================
# 工单工具（区分登录/未登录）
# ============================================

# 状态和优先级常量
VALID_STATUSES = {"待处理", "处理中", "待确认", "已完成", "已关闭"}
VALID_PRIORITIES = {"紧急", "高", "中", "低"}
SLA_MAP = {"紧急": 4, "高": 24, "中": 72, "低": 168}

# 中文展示名
STATUS_NAMES = {
    "待处理": "⏳ 待处理", "处理中": "🔧 处理中",
    "待确认": "📋 待确认", "已完成": "✅ 已完成", "已关闭": "🚫 已关闭",
}
PRIORITY_NAMES = {"紧急": "🔴 紧急", "高": "🟠 高", "中": "🟡 中", "低": "🟢 低"}


def _ensure_db():
    """确保数据库已初始化并填充了演示数据"""
    # 初始化引擎
    init_database()

    # 检查是否需要种子数据
    with get_session() as db:
        count = db.query(Ticket).count()
        if count == 0:
            # 检查是否有用户
            user_count = db.query(User).count()
            if user_count == 0:
                # 创建一个演示用户
                from src.data.auth import hash_password
                demo_user = User(
                    username="demo",
                    email="demo@example.com",
                    password_hash=hash_password("demo123"),
                    display_name="演示用户",
                    role="user",
                )
                db.add(demo_user)
                db.flush()
                logger.info("创建演示账号: demo / demo123")

            # 插入演示工单
            demo_tickets = [
                Ticket(ticket_no="TK0001", title="无法登录系统", priority="紧急",
                       status="处理中", assignee="张三", sla_hours=4,
                       description="用户反馈输入正确密码后仍无法登录，已排除网络问题",
                       user_id=1 if user_count > 0 else None),
                Ticket(ticket_no="TK0002", title="数据导出失败", priority="高",
                       status="已完成", assignee="李四", sla_hours=24,
                       description="导出报表报错500，已修复索引问题",
                       user_id=1 if user_count > 0 else None),
                Ticket(ticket_no="TK0003", title="密码重置申请", priority="中",
                       status="待处理", assignee="未分配", sla_hours=72,
                       description="员工离职交接，需重置关联账号密码",
                       user_id=None),
            ]
            for t in demo_tickets:
                db.add(t)
            db.commit()
            logger.info(f"数据库种子数据: {user_count} 用户, {len(demo_tickets)} 条工单")


def _generate_ticket_no() -> str:
    """生成工单号 TKxxxx"""
    return f"TK{uuid.uuid4().hex[:4].upper()}"


def _ticket_to_str(t: Ticket) -> str:
    """将 Ticket ORM 对象转为展示文本"""
    sla = t.sla_hours or "—"
    username = t.user.display_name or t.user.username if t.user else "匿名"
    return (
        f"工单 {t.ticket_no}\n"
        f"  标题: {t.title}\n"
        f"  状态: {t.status} | 优先级: {t.priority} | SLA: {sla}h\n"
        f"  负责人: {t.assignee} | 提交人: {username}\n"
        f"  描述: {t.description}\n"
        f"  创建: {t.created_at.strftime('%m-%d %H:%M') if t.created_at else '-'} | "
        f"更新: {t.updated_at.strftime('%m-%d %H:%M') if t.updated_at else '-'}"
    )


# ----- 查询工单（需要登录）-----

def get_ticket_status(ticket_id: str) -> str:
    """查询单个工单详情（需要登录）"""
    _ensure_db()
    user = get_current_user()
    if not user:
        return "⚠️ 请登录"
    with get_session() as db:
        t = db.query(Ticket).filter(Ticket.ticket_no == ticket_id.upper()).first()
        if not t:
            return f"未找到工单 {ticket_id}"
        return _ticket_to_str(t)


def list_tickets(status_filter: str = None) -> str:
    """列出工单列表（需要登录）"""
    _ensure_db()
    user = get_current_user()
    if not user:
        return "⚠️ 请登录"
    with get_session() as db:
        query = db.query(Ticket).order_by(Ticket.created_at.desc())

        if status_filter and status_filter in VALID_STATUSES:
            query = query.filter(Ticket.status == status_filter)

        tickets = query.limit(20).all()

        # 统计
        stats = {}
        for t in db.query(Ticket.status).distinct().all():
            status = t[0]
            count = db.query(Ticket).filter(Ticket.status == status).count()
            stats[status] = count

        if not tickets:
            label = f"「{status_filter}」" if status_filter else ""
            return f"没有{label}工单"

        lines = [f"工单列表（共 {len(tickets)} 条，总计 {sum(stats.values())} 条）"]
        lines.append(" | ".join(f"{s}:{c}" for s, c in stats.items()))
        lines.append("-" * 50)
        for t in tickets:
            username = t.user.display_name or t.user.username if t.user else "匿名"
            lines.append(
                f"[{t.ticket_no}] [{t.priority}] [{t.status}] "
                f"{t.title} | {t.assignee} | 提交:{username}"
            )
        return "\n".join(lines)


# ----- 创建工单（需要登录）-----

def create_ticket(title: str, priority: str, description: str) -> str:
    """
    创建新工单（需要登录）

    创建后自动绑定当前登录用户的 ID
    """
    _ensure_db()

    user = get_current_user()
    if not user:
        return (
            "⚠️ 请登录\n"
            "请先登录（输入 /login 用户名 密码）或注册（输入 /register 用户名 邮箱 密码）"
        )

    if priority not in VALID_PRIORITIES:
        return f"创建失败：优先级必须为 {'/'.join(VALID_PRIORITIES)}"
    if len(title.strip()) < 2:
        return "创建失败：标题至少 2 个字符"

    with get_session() as db:
        ticket_no = _generate_ticket_no()
        t = Ticket(
            ticket_no=ticket_no,
            title=title.strip(),
            priority=priority,
            status="待处理",
            description=description.strip(),
            sla_hours=SLA_MAP[priority],
            user_id=user["user_id"],
        )
        db.add(t)
        db.commit()
        db.refresh(t)

        logger.info(f"工单创建: {ticket_no} by {user['username']}")

        return (
            f"✅ 工单创建成功\n"
            f"  工单号: {t.ticket_no}\n"
            f"  标题: {t.title}\n"
            f"  优先级: {priority}（SLA: {SLA_MAP[priority]}h 内响应）\n"
            f"  状态: 待处理\n"
            f"  提交人: {user['display_name']}\n"
            f"  创建时间: {t.created_at.strftime('%Y-%m-%d %H:%M') if t.created_at else ''}"
        )


# ----- 更新工单（需要登录 + 检查所有权）-----

def update_ticket(ticket_id: str, status: str = None,
                  assignee: str = None) -> str:
    """
    更新工单（走工作流状态机）
    - 只改负责人：不走工作流
    - 改状态：走工作流校验 + 钩子
    """
    _ensure_db()
    user = get_current_user()
    if not user:
        return "⚠️ 请登录"

    if status and status not in VALID_STATUSES:
        return f"更新失败：状态必须为 {'/'.join(VALID_STATUSES)}"

    with get_session() as db:
        t = db.query(Ticket).filter(Ticket.ticket_no == ticket_id.upper()).first()
        if not t:
            return f"未找到工单 {ticket_id}"

        # 权限：管理员可操作任何工单，普通用户只能操作自己的
        is_admin = user.get("role") == "admin"
        if t.user_id and t.user_id != user["user_id"] and not is_admin:
            return f"⚠️ 权限不足：工单 {ticket_id} 属于其他用户，您只能操作自己的工单"

        # ── 状态变更：走工作流 ──
        if status and status != t.status:
            ok, msg = execute_transition(
                ticket=t,
                to_status=status,
                operator=user,
                db_session=db,
                assignee_name=assignee or t.assignee,
            )
            if not ok:
                return f"⚠️ 操作失败: {msg}"

        # ── 只改负责人：直接改 ──
        if assignee:
            t.assignee = assignee

        t.updated_at = datetime.now()
        db.commit()

        # SLA 检查
        sla_alerts = check_sla_escalation(t, db)

        logger.info(f"工单更新: {ticket_id} by {user['username']}")

        result = (
            f"✅ 工单 {t.ticket_no} 已更新\n"
            f"  当前状态: {t.status}\n"
            f"  负责人: {t.assignee}\n"
            f"  操作人: {user['display_name']}"
        )
        if sla_alerts:
            result += "\n\n⚠️ SLA 提醒:\n" + "\n".join(sla_alerts)
        return result


# ----- 删除工单（需要登录 + 检查所有权，admin 可删除任何工单）-----

def delete_ticket(ticket_id: str) -> str:
    """关闭工单（走工作流状态机 — 从当前状态→已关闭）"""
    _ensure_db()
    user = get_current_user()
    if not user:
        return "⚠️ 请登录"
    is_admin = user.get("role") == "admin"

    with get_session() as db:
        t = db.query(Ticket).filter(Ticket.ticket_no == ticket_id.upper()).first()
        if not t:
            return f"未找到工单 {ticket_id}"

        if t.user_id and t.user_id != user["user_id"] and not is_admin:
            return f"⚠️ 权限不足：工单 {ticket_id} 属于其他用户，您只能操作自己的工单"

        # 走工作流：当前状态 → 已关闭
        ok, msg = execute_transition(
            ticket=t,
            to_status="已关闭",
            operator=user,
            db_session=db,
            reason=user.get("display_name", "?") + " 手动关闭",
        )
        if not ok:
            return f"⚠️ 操作失败: {msg}"

        t.updated_at = datetime.now()
        db.commit()

        logger.info(f"工单关闭: {ticket_id} by {user['username']}")

        return (
            f"✅ 工单 {t.ticket_no} 已关闭\n"
            f"  标题: {t.title}\n"
            f"  操作人: {user['display_name']}\n"
            f"  关闭时间: {t.closed_at.strftime('%Y-%m-%d %H:%M') if t.closed_at else ''}"
        )


# ----- 物理删除（仅管理员）-----

def hard_delete_ticket(ticket_id: str) -> str:
    """物理删除工单（管理员专属）"""
    _ensure_db()
    user = get_current_user()
    if not user:
        return "⚠️ 请登录"
    if user.get("role") != "admin":
        return "⚠️ 仅管理员可彻底删除工单"

    with get_session() as db:
        t = db.query(Ticket).filter(Ticket.ticket_no == ticket_id.upper()).first()
        if not t:
            return f"未找到工单 {ticket_id}"

        db.delete(t)
        db.commit()
        logger.warning(f"工单物理删除: {ticket_id} by admin {user['username']}")
        return f"🗑️ 工单 {ticket_id} 已彻底删除"


# ============================================
# 工厂函数
# ============================================

def create_tool_registry() -> ToolRegistry:
    """创建工具注册中心（8 个工具）"""
    registry = ToolRegistry()

    # --- 公开工具 ---
    registry.register(Tool(
        name="get_current_time",
        description="获取当前日期和时间",
        parameters={"type": "object", "properties": {}, "required": []},
        func=get_current_time,
    ))

    registry.register(Tool(
        name="calculate",
        description="执行数学计算",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "数学表达式"}},
            "required": ["expression"],
        },
        func=calculate,
    ))

    # --- 工单工具 ---
    registry.register(Tool(
        name="get_ticket_status",
        description="查询某个具体工单的详情。当用户给出工单号(如TK0001)时使用。需要登录。",
        parameters={
            "type": "object",
            "properties": {"ticket_id": {"type": "string", "description": "工单号如 TK0001"}},
            "required": ["ticket_id"],
        },
        func=get_ticket_status,
    ))

    registry.register(Tool(
        name="create_ticket",
        description="创建新工单。需要登录。",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "工单标题"},
                "priority": {"type": "string", "enum": list(VALID_PRIORITIES), "description": "优先级"},
                "description": {"type": "string", "description": "问题详细描述"},
            },
            "required": ["title", "priority", "description"],
        },
        func=create_ticket,
    ))

    registry.register(Tool(
        name="update_ticket",
        description="更新工单状态或负责人。需要登录，只能操作自己的工单。",
        parameters={
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "工单号"},
                "status": {"type": "string", "enum": list(VALID_STATUSES), "description": "新状态"},
                "assignee": {"type": "string", "description": "负责人"},
            },
            "required": ["ticket_id"],
        },
        func=update_ticket,
    ))

    registry.register(Tool(
        name="delete_ticket",
        description="关闭工单。需要登录，只能操作自己的工单。",
        parameters={
            "type": "object",
            "properties": {"ticket_id": {"type": "string", "description": "工单号"}},
            "required": ["ticket_id"],
        },
        func=delete_ticket,
    ))

    registry.register(Tool(
        name="hard_delete_ticket",
        description="彻底删除工单（物理删除）。仅管理员可用。",
        parameters={
            "type": "object",
            "properties": {"ticket_id": {"type": "string", "description": "工单号"}},
            "required": ["ticket_id"],
        },
        func=hard_delete_ticket,
    ))

    registry.register(Tool(
        name="list_tickets",
        description="列出工单列表，可按状态筛选。用户说查工单、工单列表、所有工单、有哪些工单时必须用此工具。需要登录。",
        parameters={
            "type": "object",
            "properties": {"status_filter": {"type": "string", "enum": list(VALID_STATUSES), "description": "状态筛选"}},
            "required": [],
        },
        func=list_tickets,
    ))

    # --- FAQ ---
    registry.register(Tool(
        name="search_faq",
        description="搜索 FAQ 常见问题。公开，无需登录。",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词"}},
            "required": ["query"],
        },
        func=search_faq,
    ))

    return registry