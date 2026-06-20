# ============================================
# 2026-06-20 - 会话记忆管理器
# 职责：对话持久化 + 历史列表 + 恢复上下文
# 防护：单用户上限 / 全局上限 / TTL 过期 / 自动清理
# ============================================

import json
import uuid
from datetime import datetime, timedelta
from loguru import logger
from sqlalchemy import func

from src.data.database import get_session
from src.data.models import Conversation


class MemoryManager:
    """
    会话记忆管理器

    使用方式：
        mm = MemoryManager()
        sid = mm.new(user_id=1, title="登录问题")
        mm.append(sid, {"role": "user", "content": "你好"})
        history = mm.get_all(user_id=1)  # 列表
        msgs = mm.load(sid)              # 恢复上下文

    防护机制：
        - 单用户最多 PER_USER 条，超过删最旧的
        - 全局最多 GLOBAL_MAX 条，超过批量清最旧的
        - 超过 TTL_DAYS 天未访问，标记过期
        - 每次 new() 前自动触发清理
    """

    PER_USER_MAX = 50
    GLOBAL_MAX = 10000
    TTL_DAYS = 30

    # ----------------------------------------------------------------
    # 创建 & 写入
    # ----------------------------------------------------------------

    def new(self, user_id: int | None, first_message: str = "", max_per_user: int = None) -> str:
        """创建新会话 → 返回短会话 ID"""
        sid = uuid.uuid4().hex[:12]
        title = first_message[:30] if first_message else "新对话"
        now = datetime.now()

        with get_session() as db:
            # ---- 全局上限检查 ----
            total = db.query(func.count(Conversation.id)).scalar()
            if total >= self.GLOBAL_MAX:
                self._purge_global(db)

            # ---- 单用户上限检查 ----
            if user_id:
                user_count = db.query(func.count(Conversation.id)).filter(
                    Conversation.user_id == user_id
                ).scalar()
                if user_count >= (max_per_user or self.PER_USER_MAX):
                    self._purge_user(db, user_id)

            conv = Conversation(
                sid=sid, user_id=user_id, title=title,
                messages="[]", msg_count=0,
                created_at=now, accessed_at=now,
            )
            db.add(conv)
            db.commit()

        logger.debug(f"新会话: {sid} (user={user_id})")
        return sid

    def append(self, sid: str, message: dict) -> int:
        """追加一条消息 → 返回当前消息总数"""
        with get_session() as db:
            conv = db.query(Conversation).filter(
                Conversation.sid == sid, Conversation.is_expired == False
            ).first()
            if not conv:
                logger.warning(f"会话不存在或已过期: {sid}")
                return 0

            msgs = json.loads(conv.messages)
            msgs.append(message)
            conv.messages = json.dumps(msgs, ensure_ascii=False)
            conv.msg_count = len(msgs)
            conv.accessed_at = datetime.now()
            db.commit()
            return conv.msg_count

    # ----------------------------------------------------------------
    # 读取
    # ----------------------------------------------------------------

    def load(self, sid: str) -> list[dict] | None:
        """加载完整对话历史（用于恢复上下文）"""
        with get_session() as db:
            conv = db.query(Conversation).filter(
                Conversation.sid == sid, Conversation.is_expired == False
            ).first()
            if not conv:
                return None
            conv.accessed_at = datetime.now()
            db.commit()
            return json.loads(conv.messages)

    def get_all(self, user_id: int | None, limit: int = 30) -> list[dict]:
        """获取用户的会话列表"""
        with get_session() as db:
            q = db.query(Conversation).filter(
                Conversation.is_expired == False
            )
            if user_id:
                q = q.filter(Conversation.user_id == user_id)
            else:
                q = q.filter(Conversation.user_id == None)

            rows = q.order_by(Conversation.accessed_at.desc()).limit(limit).all()
            return [
                {
                    "sid": r.sid,
                    "title": r.title,
                    "msg_count": r.msg_count,
                    "created_at": r.created_at.strftime("%m-%d %H:%M") if r.created_at else "",
                    "accessed_at": r.accessed_at.strftime("%m-%d %H:%M") if r.accessed_at else "",
                }
                for r in rows
            ]

    # ----------------------------------------------------------------
    # 清理
    # ----------------------------------------------------------------

    def expire_old(self) -> int:
        """标记过期（TTL 超过的会话）→ 返回过期数量"""
        deadline = datetime.now() - timedelta(days=self.TTL_DAYS)
        with get_session() as db:
            count = db.query(Conversation).filter(
                Conversation.accessed_at < deadline,
                Conversation.is_expired == False,
            ).update({"is_expired": True})
            db.commit()
            if count:
                logger.info(f"TTL 过期: {count} 条会话")
            return count

    def clean_expired(self) -> int:
        """物理删除已过期会话 → 返回删除数量"""
        with get_session() as db:
            expired = db.query(Conversation).filter(
                Conversation.is_expired == True
            )
            count = expired.count()
            expired.delete()
            db.commit()
            if count:
                logger.info(f"清理过期: {count} 条")
            return count

    def _purge_user(self, db, user_id: int):
        """单用户超限 → 删最旧的"""
        over = (db.query(func.count(Conversation.id))
                .filter(Conversation.user_id == user_id)
                .scalar()) - self.PER_USER_MAX + 1
        if over <= 0:
            return
        rows = (db.query(Conversation)
                .filter(Conversation.user_id == user_id)
                .order_by(Conversation.accessed_at.asc())
                .limit(over).all())
        for r in rows:
            db.delete(r)
        db.flush()
        logger.info(f"单用户清理: user={user_id} 删除 {over} 条")

    def _purge_global(self, db):
        """全局超限 → 删最旧的 500 条"""
        over = self.GLOBAL_MAX // 20 or 500
        rows = (db.query(Conversation)
                .order_by(Conversation.accessed_at.asc())
                .limit(over).all())
        for r in rows:
            db.delete(r)
        db.flush()
        logger.warning(f"全局清理: 删除 {over} 条")