# ============================================
# 2026-06-20 - 工作流引擎
# 职责：状态机 + 合法转换校验 + 前后置钩子 + SLA 监听
#
# 设计理念：
#   每个业务实体定义自己的 Workflow，包含：
#     - states: 所有合法状态
#     - transitions: 允许的转换路径 + 前置条件 + 副作用
#     - hooks: 进入/离开某个状态时自动执行的函数
#
# 用法:
#   wf = TicketWorkflow()
#   ok, msg = wf.transition(ticket, "处理中")  # 从"待处理"→"处理中"
#   if ok:
#       wf.execute_hooks(ticket, "处理中")
# ============================================

from dataclasses import dataclass, field
from typing import Callable, Any
from loguru import logger


@dataclass
class Transition:
    """一次合法的状态转换定义"""
    name: str                                          # 转换名称（用于追踪）
    from_state: str                                    # 源状态
    to_state: str                                      # 目标状态
    guard: Callable[["WorkflowContext"], bool] | None = None   # 前置条件（返回 False 阻止转换）
    before: list[Callable[["WorkflowContext"], None]] = field(default_factory=list)  # 转换前动作
    after: list[Callable[["WorkflowContext"], None]] = field(default_factory=list)   # 转换后动作


@dataclass
class WorkflowContext:
    """工作流上下文——在钩子间传递的数据"""
    ticket: Any          # Ticket ORM 实例
    operator: dict       # 操作人信息 {"user_id": ..., "username": ..., "role": ...}
    extra: dict          # 额外参数（如 assignee、reason 等）
    db_session: Any      # SQLAlchemy session


class WorkflowEngine:
    """
    通用工作流引擎

    维护一张「转换表」，所有状态变更必须走这张表。
    不在表里的转换 → 直接拒绝。
    """

    def __init__(self, name: str, transitions: list[Transition]):
        self.name = name
        # 构建索引: (from_state, to_state) → Transition
        self._transitions: dict[tuple[str, str], Transition] = {}
        for t in transitions:
            self._transitions[(t.from_state, t.to_state)] = t

    @property
    def allowed_transitions(self) -> list[dict]:
        """返回所有允许的转换（用于展示）"""
        return [
            {"from": t.from_state, "to": t.to_state, "name": t.name}
            for t in self._transitions.values()
        ]

    def can_transition(self, from_state: str, to_state: str) -> bool:
        """检查某次转换是否合法"""
        return (from_state, to_state) in self._transitions

    def get_allowed_next(self, current_state: str) -> list[str]:
        """获取当前状态允许到达的下一个状态"""
        return [
            to for (frm, to) in self._transitions if frm == current_state
        ]

    def transition(self, ctx: WorkflowContext, to_state: str) -> tuple[bool, str]:
        """
        尝试执行一次状态转换

        返回: (是否成功, 消息)
        """
        from_state = ctx.ticket.status
        key = (from_state, to_state)

        trans = self._transitions.get(key)
        if trans is None:
            allowed = self.get_allowed_next(from_state)
            allowed_str = "、".join(allowed) if allowed else "无"
            return False, (
                f"不能从「{from_state}」直接转换为「{to_state}」。"
                f"允许的下一步: {allowed_str}"
            )

        # 1. 前置条件检查
        if trans.guard:
            try:
                if not trans.guard(ctx):
                    return False, f"不满足「{trans.name}」的前置条件"
            except Exception as e:
                return False, f"前置条件检查异常: {e}"

        # 2. 转换前钩子
        for hook in trans.before:
            try:
                hook(ctx)
            except Exception as e:
                logger.error(f"before 钩子异常 [{trans.name}]: {e}")

        # 3. 执行状态变更
        old_status = ctx.ticket.status
        ctx.ticket.status = to_state

        # 4. 转换后钩子
        for hook in trans.after:
            try:
                hook(ctx)
            except Exception as e:
                logger.error(f"after 钩子异常 [{trans.name}]: {e}")

        logger.info(
            f"状态流转 [{ctx.ticket.ticket_no}]: "
            f"{old_status} → {to_state} ({trans.name}) by {ctx.operator.get('username', '?')}"
        )
        return True, f"「{trans.name}」执行成功: {old_status} → {to_state}"
