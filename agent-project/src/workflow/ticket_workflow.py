# ============================================
# 2026-06-20 - 工单生命周期工作流
# 职责：定义工单状态机 + 合法转换 + 前后置钩子 + SLA 升级
#
# 状态流转图:
#                  ┌──────────┐
#          ┌──────→│   待处理   │
#          │       └────┬─────┘
#          │            │ 接单(start_processing)
#          │            ↓
#          │       ┌──────────┐
#          │ 退回  │   处理中   │──取消──→ ─┐
#          │ (退回)│          │          │
#          │       └────┬─────┘          │
#          │            │ 提交(submit)    │
#          │            ↓                │
#          │       ┌──────────┐          │
#          │       │   待确认   │──取消──→  │
#          │       └────┬─────┘          │
#          │            │ 确认(confirm)   │
#          │            ↓                │
#          │       ┌──────────┐          │
#          │       │   已完成   │──归档──→  │
#          │       └────┬─────┘          │
#          │            │ 归档(archive)   │
#          │            ↓                ↓
#          │       ┌──────────┐     ┌──────────┐
#          └─ 重开 │   已关闭   │     │   已关闭   │
#    (reopen)     └──────────┘     └──────────┘
# ============================================

from datetime import datetime, timedelta
from dataclasses import dataclass
from loguru import logger

from src.workflow.engine import (
    WorkflowEngine, Transition, WorkflowContext,
)


# ═══════════════════════════════════════════════
# 状态与优先级常量
# ═══════════════════════════════════════════════

STATUS_LABELS = {
    "待处理": "⏳ 待处理",
    "处理中": "🔧 处理中",
    "待确认": "📋 待确认",
    "已完成": "✅ 已完成",
    "已关闭": "🚫 已关闭",
}

SLA_HOURS = {"紧急": 4, "高": 24, "中": 72, "低": 168}
SLA_RESPONSE = {"紧急": 0.5, "高": 2, "中": 8, "低": 24}  # 首次响应时限（小时）


# ═══════════════════════════════════════════════
# 前置条件守卫（guard 函数）
# ═══════════════════════════════════════════════

def _is_admin_or_creator(ctx: WorkflowContext) -> bool:
    """管理员 或 工单创建者"""
    op = ctx.operator
    if op.get("role") == "admin":
        return True
    user_id = op.get("user_id") or op.get("id")
    return str(ctx.ticket.user_id) == str(user_id)


def _has_assignee(ctx: WorkflowContext) -> bool:
    """工单已有负责人"""
    return bool(ctx.ticket.assignee and ctx.ticket.assignee != "未分配")


def _is_admin(ctx: WorkflowContext) -> bool:
    """仅管理员"""
    return ctx.operator.get("role") == "admin"


# ═══════════════════════════════════════════════
# 钩子函数（before / after）
# ═══════════════════════════════════════════════

def _set_started_at(ctx: WorkflowContext):
    """记录处理开始时间"""
    ctx.ticket.processing_started_at = ctx.ticket.processing_started_at or datetime.now()


def _set_closed_at(ctx: WorkflowContext):
    """记录关闭时间"""
    ctx.ticket.closed_at = datetime.now()


def _clear_assignee(ctx: WorkflowContext):
    """退回时清空负责人"""
    ctx.ticket.assignee = "未分配"


def _log_transition(ctx: WorkflowContext):
    """记录流转日志"""
    op_name = ctx.operator.get("username") or ctx.operator.get("display_name", "?")
    reason = ctx.extra.get("reason", "")
    logger.info(
        f"工单流转 [{ctx.ticket.ticket_no}]: "
        f"{ctx.ticket.status} by {op_name}"
        + (f" (原因: {reason})" if reason else "")
    )


# ═══════════════════════════════════════════════
# 定义所有允许的转换
# ═══════════════════════════════════════════════

TRANSITIONS = [
    # ── 从「待处理」出发 ──
    Transition(
        name="接单",
        from_state="待处理", to_state="处理中",
        guard=_is_admin_or_creator,
        before=[_log_transition],
        after=[_set_started_at],
    ),
    Transition(
        name="取消工单",
        from_state="待处理", to_state="已关闭",
        guard=_is_admin_or_creator,
        before=[_log_transition],
        after=[_set_closed_at],
    ),

    # ── 从「处理中」出发 ──
    Transition(
        name="提交处理结果",
        from_state="处理中", to_state="待确认",
        guard=_is_admin_or_creator,
        before=[_log_transition],
    ),
    Transition(
        name="取消工单",
        from_state="处理中", to_state="已关闭",
        guard=_is_admin_or_creator,
        before=[_log_transition],
        after=[_set_closed_at],
    ),

    # ── 从「待确认」出发 ──
    Transition(
        name="用户确认",
        from_state="待确认", to_state="已完成",
        guard=_is_admin_or_creator,
        before=[_log_transition],
    ),
    Transition(
        name="退回重做",
        from_state="待确认", to_state="处理中",
        guard=_is_admin,
        before=[_log_transition],
        after=[_clear_assignee],
    ),
    Transition(
        name="取消工单",
        from_state="待确认", to_state="已关闭",
        guard=_is_admin_or_creator,
        before=[_log_transition],
        after=[_set_closed_at],
    ),

    # ── 从「已完成」出发 ──
    Transition(
        name="归档",
        from_state="已完成", to_state="已关闭",
        guard=_is_admin_or_creator,
        before=[_log_transition],
        after=[_set_closed_at],
    ),

    # ── 从「已关闭」出发（重新打开） ──
    Transition(
        name="重新打开",
        from_state="已关闭", to_state="待处理",
        guard=_is_admin_or_creator,
        before=[_log_transition],
    ),
]

# ═══════════════════════════════════════════════
# SLA 升级规则
# ═══════════════════════════════════════════════

def check_sla_escalation(ticket, db_session) -> list[str]:
    """
    检查工单 SLA 状态，返回告警列表

    规则:
      1. 首次响应超时 → 优先级提升一档 + 通知
      2. 处理超时（处理中超过 sla_hours 仍未解决）→ 继续升级
    """
    alerts = []
    now = datetime.now()

    sla_h = ticket.sla_hours or 72
    created = ticket.created_at
    started = getattr(ticket, "processing_started_at", None)

    # --- 首次响应检查 ---
    response_h = SLA_RESPONSE.get(ticket.priority, 8)
    if created and ticket.status in ("待处理",):
        elapsed = (now - created).total_seconds() / 3600
        if elapsed > response_h:
            alerts.append(
                f"⚠️ [{ticket.ticket_no}] {ticket.title}: "
                f"已等待 {elapsed:.1f}h，超过响应时限 {response_h}h，建议升级"
            )

    # --- 处理超时检查 ---
    if started and ticket.status in ("处理中", "待确认"):
        elapsed = (now - started).total_seconds() / 3600
        if elapsed > sla_h:
            alerts.append(
                f"🚨 [{ticket.ticket_no}] {ticket.title}: "
                f"处理已耗时 {elapsed:.1f}h，超过 SLA {sla_h}h，请立即处理"
            )

    # --- 自动升级 ---
    priority_order = {"低": 1, "中": 2, "高": 3, "紧急": 4}
    current_level = priority_order.get(ticket.priority, 2)

    if started and ticket.status in ("处理中", "待确认"):
        elapsed = (now - started).total_seconds() / 3600
        # 超过 SLA 的 1.5 倍自动升级
        if elapsed > sla_h * 1.5 and current_level < 4:
            new_priority = {1: "中", 2: "高", 3: "紧急"}.get(current_level, "紧急")
            old_priority = ticket.priority
            ticket.priority = new_priority
            ticket.sla_hours = SLA_HOURS[new_priority]
            db_session.commit()
            alerts.append(
                f"🔺 [{ticket.ticket_no}] {ticket.title}: "
                f"SLA 严重超时，优先级自动升级 {old_priority} → {new_priority}"
            )
            logger.warning(
                f"SLA 自动升级: {ticket.ticket_no} {old_priority}→{new_priority}"
            )

    return alerts


def scan_all_sla(db_session) -> list[str]:
    """
    扫描所有未关闭工单的 SLA 状态

    用途：定时任务或 /health 检查时调用
    """
    from src.data.models import Ticket as TM

    all_alerts = []
    tickets = (
        db_session.query(TM)
        .filter(TM.status.notin_(["已关闭"]))
        .all()
    )
    for t in tickets:
        alerts = check_sla_escalation(t, db_session)
        all_alerts.extend(alerts)
    return all_alerts


# ═══════════════════════════════════════════════
# 工单工作流实例（单例）
# ═══════════════════════════════════════════════

_ticket_workflow: WorkflowEngine | None = None


def get_ticket_workflow() -> WorkflowEngine:
    global _ticket_workflow
    if _ticket_workflow is None:
        _ticket_workflow = WorkflowEngine("工单生命周期", TRANSITIONS)
        logger.info(
            f"工单工作流就绪: {len(TRANSITIONS)} 条转换规则, "
            f"{len(set(t.from_state for t in TRANSITIONS))} 个状态"
        )
    return _ticket_workflow


# ═══════════════════════════════════════════════
# 便捷方法
# ═══════════════════════════════════════════════

def execute_transition(
    ticket,
    to_status: str,
    operator: dict,
    db_session,
    **extra,
) -> tuple[bool, str]:
    """
    执行一次工单流转（对外的统一入口）

    参数:
        ticket: Ticket ORM 实例
        to_status: 目标状态
        operator: 操作人信息 {"user_id":..., "username":..., "role":...}
        db_session: SQLAlchemy session
        **extra: 额外参数（assignee, reason 等）

    返回:
        (是否成功, 消息)
    """
    wf = get_ticket_workflow()
    ctx = WorkflowContext(
        ticket=ticket,
        operator=operator,
        extra=extra,
        db_session=db_session,
    )
    return wf.transition(ctx, to_status)
