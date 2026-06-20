# ============================================
# 2026-06-19 - 用户认证模块
# 职责：注册、登录、会话管理
#
# 安全设计：
#   - 密码用 PBKDF2 + SHA256 哈希（不可逆）
#   - 会话用内存 dict（单机），生产用 Redis
#   - 登录 30 分钟无操作自动过期
# ============================================

import hashlib
import uuid
import os
from datetime import datetime, timedelta
from loguru import logger

from sqlalchemy import func
from src.data.database import get_session, init_database
from src.data.models import User


# ============================================
# 密码哈希
# ============================================

def hash_password(password: str) -> str:
    """
    PBKDF2 + SHA256 哈希密码

    为什么不用 MD5/SHA1？
      - 太弱，彩虹表一查就破
    为什么不用 bcrypt？
      - 需要额外装包，PBKDF2 是 Python 内置，够用
    """
    salt = os.urandom(32)                    # 32字节随机盐
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        100000,                               # 10万次迭代（企业建议值）
    )
    # 存储格式: salt_hex$hash_hex
    return salt.hex() + "$" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    """验证密码"""
    try:
        salt_hex, hash_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            100000,
        )
        return dk.hex() == hash_hex
    except Exception:
        return False


# ============================================
# 会话管理
# ============================================

class SessionManager:
    """
    用户会话管理器

    存储: 内存 dict → 生产环境换 Redis
    过期: 30 分钟无操作自动失效
    """

    SESSION_TIMEOUT_MINUTES = 30

    def __init__(self):
        # 内存存储: {token: {"user_id": 1, "username": "张三", "expires_at": datetime}}
        self._sessions: dict[str, dict] = {}

    def create_session(self, user: User) -> str:
        """为用户创建会话，返回 token"""
        token = uuid.uuid4().hex
        self._sessions[token] = {
            "user_id": user.id,
            "username": user.username,
            "display_name": user.display_name or user.username,
            "role": user.role,
            "created_at": datetime.now(),
            "expires_at": datetime.now() + timedelta(minutes=self.SESSION_TIMEOUT_MINUTES),
        }
        # 清理过期 session
        self._cleanup()
        logger.info(f"会话创建: {user.username} (过期={self.SESSION_TIMEOUT_MINUTES}min)")
        return token

    def get_user(self, token: str) -> dict | None:
        """通过 token 获取用户信息，过期返回 None"""
        session = self._sessions.get(token)
        if not session:
            return None
        if datetime.now() > session["expires_at"]:
            del self._sessions[token]
            return None
        # 续期
        session["expires_at"] = datetime.now() + timedelta(minutes=self.SESSION_TIMEOUT_MINUTES)
        return {
            "user_id": session["user_id"],
            "username": session["username"],
            "display_name": session["display_name"],
            "role": session["role"],
        }

    def destroy_session(self, token: str) -> None:
        """销毁会话（登出）"""
        self._sessions.pop(token, None)
        logger.info("会话已销毁")

    def _cleanup(self):
        """清理过期会话"""
        now = datetime.now()
        expired = [t for t, s in self._sessions.items() if now > s["expires_at"]]
        for t in expired:
            del self._sessions[t]

    @property
    def active_count(self) -> int:
        self._cleanup()
        return len(self._sessions)


# 全局单例
session_manager = SessionManager()


# ============================================
# 认证业务逻辑
# ============================================

class AuthService:
    """认证服务"""

    @staticmethod
    def register(username: str, email: str, password: str,
                 display_name: str = None) -> dict:
        """
        注册新用户

        返回:
            {"success": True, "message": "注册成功", "user": {...}}
        """
        # 校验
        import re
        if len(username.strip()) < 2:
            return {"success": False, "message": "用户名至少 2 个字符"}
        if len(password) < 6:
            return {"success": False, "message": "密码至少 6 位"}
        # 正规邮箱格式校验
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email.strip()):
            return {"success": False, "message": "邮箱格式不正确，请输入有效的邮箱地址（如 name@example.com）"}
        # 检查常见临时邮箱域名
        disposable_domains = {"mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com"}
        domain = email.strip().split("@")[-1].lower()
        if domain in disposable_domains:
            return {"success": False, "message": "不支持使用临时邮箱注册，请使用正式邮箱"}

        with get_session() as db:
            # 只检查邮箱重复（允许用户名重复，用 id 区分）
            existing = db.query(User).filter(User.email == email.strip()).first()
            if existing:
                return {"success": False, "message": "该邮箱已被注册，请直接登录或使用其他邮箱"}

            # 创建用户
            user = User(
                username=username.strip(),
                email=email.strip(),
                password_hash=hash_password(password),
                display_name=display_name or username.strip(),
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            logger.info(f"新用户注册: {user.username} (id={user.id})")
            return {
                "success": True,
                "message": "注册成功",
                "user": user.to_dict(),
            }

    @staticmethod
    def login(username: str, password: str) -> dict:
        """
        登录

        返回:
            {"success": True, "token": "...", "user": {...}}
        """
        with get_session() as db:
            user = db.query(User).filter(User.username == username.strip()).first()
            if not user:
                return {"success": False, "message": "用户名或密码错误"}

            if not verify_password(password, user.password_hash):
                return {"success": False, "message": "用户名或密码错误"}

            token = session_manager.create_session(user)
            logger.info(f"用户登录: {user.username}")

            return {
                "success": True,
                "token": token,
                "user": user.to_dict(),
            }

    @staticmethod
    def logout(token: str) -> dict:
        """登出"""
        session_manager.destroy_session(token)
        return {"success": True, "message": "已登出"}

    @staticmethod
    def authenticate(token: str) -> dict | None:
        """验证 token 是否有效，返回用户信息或 None"""
        return session_manager.get_user(token)

    @staticmethod
    def get_user_by_id(user_id: int) -> dict | None:
        """通过 ID 获取用户信息"""
        with get_session() as db:
            user = db.query(User).filter(User.id == user_id).first()
            return user.to_dict() if user else None


# ============================================
# API 密钥认证（客户端层）
# ============================================

def verify_api_key(api_key: str | None) -> bool:
    """验证 API 密钥是否有效"""
    from src.config.settings import settings
    if not api_key:
        return False
    expected = settings.APP_API_KEY
    if not expected:
        return True  # 未配置 API Key 时默认放行（开发模式）
    # 常量时间比较（防时序攻击）
    import hmac
    return hmac.compare_digest(api_key, expected)