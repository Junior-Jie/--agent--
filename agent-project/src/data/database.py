# ============================================
# 2026-06-19 - 数据库引擎
# 职责：创建 SQLAlchemy 引擎和会话工厂
# ============================================

import os
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from loguru import logger

Base = declarative_base()
_engine = None
_SessionLocal = None


def init_database(db_path: str = None):
    """初始化数据库引擎并自动建表"""
    global _engine, _SessionLocal

    if db_path is None:
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent
        db_path = str(root / "data" / "tickets.db")

    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    db_url = f"sqlite:///{db_path}"
    _engine = create_engine(
        db_url,
        echo=False,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(_engine)

    # 迁移：添加 processing_started_at 列（SQLite 不支持 ALTER COLUMN 的 ADD IF NOT EXISTS）
    with _engine.connect() as conn:
        import sqlite3
        try:
            # 只在 SQLite 下执行
            if isinstance(conn.connection.connection, sqlite3.Connection):
                cursor = conn.connection.connection.cursor()
                cursor.execute("PRAGMA table_info(tickets)")
                cols = [row[1] for row in cursor.fetchall()]
                if "processing_started_at" not in cols:
                    cursor.execute("ALTER TABLE tickets ADD COLUMN processing_started_at DATETIME")
                    logger.info("迁移: tickets 表添加 processing_started_at 列")
                cursor.close()
        except Exception:
            pass  # 非 SQLite 或列已存在则跳过

    logger.info(f"数据库初始化完成: {db_url}")


@contextmanager
def get_session():
    """数据库会话（上下文管理器）"""
    if _SessionLocal is None:
        init_database()
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_raw_session():
    """获取原始会话（非上下文管理器，记得手动 close）"""
    if _SessionLocal is None:
        init_database()
    return _SessionLocal()


def reset_database():
    """清空所有表（测试用）"""
    global _engine
    if _engine is not None:
        Base.metadata.drop_all(_engine)
        Base.metadata.create_all(_engine)
        logger.info("数据库已重置")