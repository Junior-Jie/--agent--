# ============================================
# 2026-06-19 - ORM 数据模型
# 职责：定义 User（用户）和 Ticket（工单）的数据库表结构
# ============================================

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Index, Boolean
)
from sqlalchemy.orm import relationship
from src.data.database import Base


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False, index=True)       # 允许重名
    email = Column(String(120), unique=True, nullable=False)       # 邮箱唯一
    password_hash = Column(String(256), nullable=False)  # PBKDF2 哈希
    display_name = Column(String(50), nullable=True)      # 显示名
    role = Column(String(20), default="user")             # user / admin
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 关联工单
    tickets = relationship("Ticket", back_populates="user", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "display_name": self.display_name or self.username,
            "role": self.role,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
        }

    def __repr__(self):
        return f"<User {self.username}>"


class Ticket(Base):
    """工单表"""
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_no = Column(String(10), unique=True, nullable=False, index=True)  # TKxxxx
    title = Column(String(200), nullable=False)
    priority = Column(String(10), nullable=False, default="中")   # 紧急/高/中/低
    status = Column(String(10), nullable=False, default="待处理")
    description = Column(Text, default="")
    assignee = Column(String(50), default="未分配")
    sla_hours = Column(Integer, default=72)

    # 关联用户
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    user = relationship("User", back_populates="tickets")

    processing_started_at = Column(DateTime, nullable=True)  # 开始处理时间
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    closed_at = Column(DateTime, nullable=True)

    # 复合索引：按用户+状态快速查询
    __table_args__ = (
        Index("idx_user_status", "user_id", "status"),
        Index("idx_status_created", "status", "created_at"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "ticket_no": self.ticket_no,
            "title": self.title,
            "priority": self.priority,
            "status": self.status,
            "description": self.description,
            "assignee": self.assignee,
            "sla_hours": self.sla_hours,
            "user_id": self.user_id,
            "username": self.user.display_name or self.user.username if self.user else "匿名",
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else "",
            "closed_at": self.closed_at.strftime("%Y-%m-%d %H:%M") if self.closed_at else "",
            "processing_started_at": self.processing_started_at.strftime("%Y-%m-%d %H:%M") if self.processing_started_at else "",
        }

    def __repr__(self):
        return f"<Ticket {self.ticket_no}: {self.title}>"


class Conversation(Base):
    """对话记忆表 — 持久化 Agent 对话历史"""
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sid = Column(String(12), unique=True, nullable=False, index=True)     # 短会话 ID
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)       # NULL = 游客
    title = Column(String(80), nullable=False, default="新对话")            # 自动从第一句话截取
    messages = Column(Text, nullable=False, default="[]")                  # JSON 数组
    created_at = Column(DateTime, default=datetime.now)
    accessed_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    is_expired = Column(Boolean, default=False)                            # TTL 过期标记
    msg_count = Column(Integer, default=0)                                 # 消息条数