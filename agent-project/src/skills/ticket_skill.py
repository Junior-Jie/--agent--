# ============================================
# 2026-06-20 - 工单技能
# 6 工具：创建/查询/列表/更新/关闭/统计
# ============================================

import uuid
from datetime import datetime
from src.skills.base import Skill
from src.tools.base import Tool
from src.data.database import get_session
from src.data.models import Ticket
from src.data.context import get_current_user

VALID_PRIORITIES = {"紧急", "高", "中", "低"}
VALID_STATUSES = {"待处理", "处理中", "待确认", "已完成", "已关闭"}
SLA_MAP = {"紧急": 4, "高": 24, "中": 72, "低": 168}


class TicketSkill(Skill):
    @property
    def name(self) -> str: return "ticket"

    @property
    def description(self) -> str: return "工单全生命周期管理（CRUD + 统计）"

    @property
    def prompt_hint(self) -> str: return (
        "- 工单: 查工单/开工单/改工单/关工时使用 create_ticket/get_ticket/list_tickets/"
        "update_ticket/delete_ticket/get_ticket_stats。请求缺参数时请向用户追问。"
    )

    def get_tools(self) -> list[Tool]:
        return [
            Tool("create_ticket", "创建新工单。用户要求开工单、提交问题时使用。",
                 {"type": "object", "properties": {
                     "title": {"type": "string", "description": "工单标题"},
                     "priority": {"type": "string", "enum": list(VALID_PRIORITIES), "description": "优先级"},
                     "description": {"type": "string", "description": "问题详情"},
                 }, "required": ["title", "priority", "description"]},
                 self._create),

            Tool("get_ticket", "查询单个工单详情。用户给出工单号时使用。",
                 {"type": "object", "properties": {
                     "ticket_no": {"type": "string", "description": "工单号"}
                 }, "required": ["ticket_no"]},
                 self._get),

            Tool("list_tickets", "列出工单列表。用户说查工单/工单列表/有哪些工单时使用。",
                 {"type": "object", "properties": {
                     "status": {"type": "string", "enum": list(VALID_STATUSES), "description": "状态筛选"}
                 }, "required": []},
                 self._list),

            Tool("update_ticket", "更新工单状态或分配负责人。",
                 {"type": "object", "properties": {
                     "ticket_no": {"type": "string", "description": "工单号"},
                     "new_status": {"type": "string", "enum": list(VALID_STATUSES), "description": "新状态"},
                     "assignee": {"type": "string", "description": "负责人"},
                 }, "required": ["ticket_no"]},
                 self._update),

            Tool("delete_ticket", "关闭工单（软删除）。",
                 {"type": "object", "properties": {
                     "ticket_no": {"type": "string", "description": "工单号"}
                 }, "required": ["ticket_no"]},
                 self._delete),

            Tool("get_ticket_stats", "获取工单统计概览。",
                 {"type": "object", "properties": {}, "required": []},
                 self._stats),
        ]

    def _create(self, title: str, priority: str, description: str) -> str:
        user = get_current_user()
        if not user: return "⚠️ 请登录"
        if priority not in VALID_PRIORITIES: return f"优先级必须为 {'/'.join(VALID_PRIORITIES)}"
        from src.data.database import get_raw_session
        db = get_raw_session()
        try:
            tno = f"TK{uuid.uuid4().hex[:4].upper()}"
            t = Ticket(ticket_no=tno, title=title.strip(), priority=priority,
                       status="待处理", description=description.strip(),
                       sla_hours=SLA_MAP[priority],
                       user_id=user.get("user_id") or user.get("id"))
            db.add(t); db.commit(); db.refresh(t)
            return f"✅ 工单 {tno} 创建成功：{title} [{priority}，SLA:{SLA_MAP[priority]}h]"
        finally:
            db.close()

    def _get(self, ticket_no: str) -> str:
        with get_session() as db:
            t = db.query(Ticket).filter(Ticket.ticket_no == ticket_no.upper()).first()
            if not t: return f"未找到工单 {ticket_no}"
            u = t.user.display_name or t.user.username if t.user else "匿名"
            return (f"工单 {t.ticket_no}: {t.title}\n  状态:{t.status} 优先级:{t.priority} "
                    f"负责人:{t.assignee} 提交:{u}")

    def _list(self, status: str = "") -> str:
        with get_session() as db:
            q = db.query(Ticket).order_by(Ticket.created_at.desc())
            if status: q = q.filter(Ticket.status == status)
            tickets = q.limit(20).all()
            if not tickets: return "暂无工单"
            lines = [f"工单列表（{len(tickets)}条）:"]
            for t in tickets:
                u = t.user.display_name or t.user.username if t.user else "?"
                lines.append(f"  [{t.ticket_no}] [{t.priority}] [{t.status}] {t.title} | {t.assignee} | {u}")
            return "\n".join(lines)

    def _update(self, ticket_no: str, new_status: str = "", assignee: str = "") -> str:
        with get_session() as db:
            t = db.query(Ticket).filter(Ticket.ticket_no == ticket_no.upper()).first()
            if not t: return f"未找到工单 {ticket_no}"
            if new_status and new_status in VALID_STATUSES:
                t.status = new_status
                if new_status == "已关闭": t.closed_at = datetime.now()
            if assignee: t.assignee = assignee
            t.updated_at = datetime.now(); db.commit()
            return f"✅ 工单 {ticket_no} 已更新: 状态={t.status}, 负责人={t.assignee}"

    def _delete(self, ticket_no: str) -> str:
        with get_session() as db:
            t = db.query(Ticket).filter(Ticket.ticket_no == ticket_no.upper()).first()
            if not t: return f"未找到工单 {ticket_no}"
            t.status = "已关闭"; t.closed_at = datetime.now(); t.updated_at = datetime.now()
            db.commit()
            return f"✅ 工单 {ticket_no} 已关闭"

    def _stats(self) -> str:
        with get_session() as db:
            lines = ["工单统计:"]
            total = sum(db.query(Ticket).filter(Ticket.status == s).count() for s in VALID_STATUSES)
            lines.append(f"  总计: {total}")
            for s in VALID_STATUSES:
                lines.append(f"  {s}: {db.query(Ticket).filter(Ticket.status == s).count()}")
            return "\n".join(lines)