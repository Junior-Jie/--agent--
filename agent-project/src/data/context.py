# ============================================
# 2026-06-20 - 请求上下文（contextvars 跨线程版）
# Python 3.7+ 的 contextvars 自动跨 asyncio/task/线程传播
# ============================================

import contextvars

_current_user: contextvars.ContextVar = contextvars.ContextVar("current_user", default=None)


def set_current_user(user: dict | None) -> None:
    _current_user.set(user)


def get_current_user() -> dict | None:
    return _current_user.get()


def clear_current_user() -> None:
    _current_user.set(None)


def is_authenticated() -> bool:
    return get_current_user() is not None