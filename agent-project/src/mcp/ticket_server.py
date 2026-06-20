# ============================================
# 2026-06-19 - 工单 MCP Server
# 职责：将工单 CRUD 暴露为 MCP 工具
#       外部 Agent 可通过 MCP Client 发现并调用
#
# 启动方式:
#   python src/mcp/ticket_server.py
#   或由 MCP Client 作为子进程启动
#
# 暴露的工具（6个）:
#   create_ticket, get_ticket, list_tickets,
#   update_ticket, delete_ticket, get_ticket_stats
#
# 架构意义：
#   工单服务可以独立部署、独立扩缩、独立更新
#   不依赖 Agent 进程，多个 Agent 实例共享一个工单 MCP Server
# ============================================

import uuid
from datetime import datetime
from mcp.server import FastMCP
from sqlalchemy.orm import Session

# ===== 数据库（复用已有的 models + database）=====
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.data.database import init_database, get_raw_session
from src.data.models import User, Ticket

# 初始化数据库
init_database()

# 创建 FastMCP 服务器
mcp = FastMCP("Ticket Management Service")

# 常量
VALID_STATUSES = {"待处理", "处理中", "待确认", "已完成", "已关闭"}
VALID_PRIORITIES = {"紧急", "高", "中", "低"}
SLA_MAP = {"紧急": 4, "高": 24, "中": 72, "低": 168}


def _format_ticket(t: Ticket) -> str:
    username = t.user.display_name or t.user.username if t.user else "匿名"
    return (
        f"工单 {t.ticket_no}\n"
        f"  标题: {t.title}\n"
        f"  状态: {t.status} | 优先级: {t.priority} | SLA: {t.sla_hours or '—'}h\n"
        f"  负责人: {t.assignee} | 提交人: {username}\n"
        f"  描述: {t.description}\n"
        f"  创建: {t.created_at.strftime('%m-%d %H:%M') if t.created_at else '-'}"
    )


# ============================================
# 工具 1: 创建工单
# ============================================

@mcp.tool()
def create_ticket(title: str, priority: str, description: str) -> str:
    """
    创建新的服务工单。用户要求开工单、提交问题、上报故障时使用。

    参数:
        title: 工单标题，简述问题
        priority: 优先级（紧急/高/中/低）
        description: 问题详细描述
    """
    if priority not in VALID_PRIORITIES:
        return f"创建失败：优先级必须为 {'/'.join(VALID_PRIORITIES)}"
    if len(title.strip()) < 2:
        return "创建失败：标题至少 2 个字符"

    db = get_raw_session()
    try:
        ticket_no = f"TK{uuid.uuid4().hex[:4].upper()}"
        t = Ticket(
            ticket_no=ticket_no,
            title=title.strip(),
            priority=priority,
            status="待处理",
            description=description.strip(),
            sla_hours=SLA_MAP[priority],
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return (
            f"✅ 工单创建成功\n"
            f"  工单号: {t.ticket_no}\n"
            f"  标题: {t.title}\n"
            f"  优先级: {priority}（SLA: {SLA_MAP[priority]}h）\n"
            f"  状态: 待处理\n"
            f"  创建时间: {t.created_at.strftime('%Y-%m-%d %H:%M')}"
        )
    finally:
        db.close()


# ============================================
# 工具 2: 查询工单
# ============================================

@mcp.tool()
def get_ticket(ticket_no: str) -> str:
    """
    查询单个工单的详细信息。用户给出工单号(如TK0001)时使用。

    参数:
        ticket_no: 工单编号，如 TK0001
    """
    db = get_raw_session()
    try:
        t = db.query(Ticket).filter(Ticket.ticket_no == ticket_no.upper()).first()
        if not t:
            ids = [r.ticket_no for r in db.query(Ticket).limit(10).all()]
            return f"未找到工单 {ticket_no}。现有工单: {', '.join(ids)}" if ids else f"未找到工单 {ticket_no}"
        return _format_ticket(t)
    finally:
        db.close()


# ============================================
# 工具 3: 工单列表
# ============================================

@mcp.tool()
def list_tickets(status: str = "") -> str:
    """
    列出工单列表，可按状态筛选。用户说查工单、工单列表时必须用此工具。

    参数:
        status: 可选的状态筛选（待处理/处理中/待确认/已完成/已关闭），不传则全部
    """
    db = get_raw_session()
    try:
        query = db.query(Ticket).order_by(Ticket.created_at.desc())
        if status and status in VALID_STATUSES:
            query = query.filter(Ticket.status == status)

        tickets = query.limit(20).all()

        # 统计
        stats = {}
        for row in db.query(Ticket.status, db.query(Ticket).filter(Ticket.status == Ticket.status).count()).distinct():
            pass  # 简化统计
        for s in VALID_STATUSES:
            c = db.query(Ticket).filter(Ticket.status == s).count()
            if c > 0:
                stats[s] = c

        if not tickets:
            return f"没有{status + ' ' if status else ''}工单"

        lines = [f"工单列表（共 {len(tickets)} 条）"]
        lines.append(" | ".join(f"{s}:{c}" for s, c in stats.items()))
        lines.append("-" * 50)
        for t in tickets:
            username = t.user.display_name or t.user.username if t.user else "匿名"
            lines.append(
                f"[{t.ticket_no}] [{t.priority}] [{t.status}] "
                f"{t.title} | {t.assignee} | 提交:{username}"
            )
        return "\n".join(lines)
    finally:
        db.close()


# ============================================
# 工具 4: 更新工单
# ============================================

@mcp.tool()
def update_ticket(ticket_no: str, new_status: str = "", assignee: str = "") -> str:
    """
    更新工单状态或负责人。

    参数:
        ticket_no: 工单编号
        new_status: 新状态（待处理/处理中/待确认/已完成/已关闭）
        assignee: 新负责人姓名
    """
    db = get_raw_session()
    try:
        t = db.query(Ticket).filter(Ticket.ticket_no == ticket_no.upper()).first()
        if not t:
            return f"未找到工单 {ticket_no}"

        changes = []
        if new_status and new_status in VALID_STATUSES:
            t.status = new_status
            changes.append(f"状态→{new_status}")
            if new_status == "已关闭":
                t.closed_at = datetime.now()
        if assignee:
            t.assignee = assignee
            changes.append(f"负责人→{assignee}")

        if not changes:
            return "未指定任何更新项"

        t.updated_at = datetime.now()
        db.commit()

        return (
            f"✅ 工单{ticket_no}已更新: {', '.join(changes)}\n"
            f"  当前状态: {t.status} | 负责人: {t.assignee}"
        )
    finally:
        db.close()


# ============================================
# 工具 5: 关闭工单
# ============================================

@mcp.tool()
def delete_ticket(ticket_no: str) -> str:
    """
    关闭工单（软删除）。用户要求撤销工单、关闭工单时使用。

    参数:
        ticket_no: 工单编号
    """
    db = get_raw_session()
    try:
        t = db.query(Ticket).filter(Ticket.ticket_no == ticket_no.upper()).first()
        if not t:
            return f"未找到工单 {ticket_no}"

        t.status = "已关闭"
        t.closed_at = datetime.now()
        t.updated_at = datetime.now()
        db.commit()

        return (
            f"✅ 工单 {ticket_no} 已关闭\n"
            f"  标题: {t.title}\n"
            f"  关闭时间: {t.closed_at.strftime('%Y-%m-%d %H:%M')}"
        )
    finally:
        db.close()


# ============================================
# 工具 6: 工单统计
# ============================================

@mcp.tool()
def get_ticket_stats() -> str:
    """
    获取工单统计概览（各状态数量）。
    用户问工单概况、统计、多少工单时使用。
    """
    db = get_raw_session()
    try:
        stats = {}
        for s in VALID_STATUSES:
            count = db.query(Ticket).filter(Ticket.status == s).count()
            stats[s] = count

        total = sum(stats.values())
        lines = [f"工单统计（总计 {total} 条）", "-" * 30]
        icons = {"待处理": "⏳", "处理中": "🔧", "待确认": "📋", "已完成": "✅", "已关闭": "🚫"}
        for s in VALID_STATUSES:
            bar = "█" * min(stats[s], 20)
            lines.append(f"  {icons.get(s, '')} {s}: {stats[s]} {bar}")
        return "\n".join(lines)
    finally:
        db.close()


# ============================================
# 启动服务器
# ============================================
if __name__ == "__main__":
    import sys as _sys
    _sys.stderr.write("Ticket MCP Server started (stdio) — 6 tools\n")
    _sys.stderr.flush()
    mcp.run(transport="stdio")