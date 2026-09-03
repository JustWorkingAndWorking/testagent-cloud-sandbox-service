"""
SQLAlchemy Engine / Session 工厂与迁移入口（v4 §6.3）。

- SQLite 文件路径 = `Constants.DB_PATH`（v4 §5.2）。
- 写操作一律在事务中执行：`session_scope` 统一 commit / rollback / close。
- `init_db()` 由 Alembic 执行迁移，进程入口在启动阶段调用。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import Constants

__all__ = [
    "get_schema_version",
    "init_db",
    "session_scope",
]

_db_path = Constants.DB_PATH.value

if _db_path != ":memory:":
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{_db_path}",
    connect_args={"check_same_thread": False},
)

SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """开启一个事务性 Session：正常退出提交，异常回滚，始终关闭。"""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """将数据库升级到最新架构版本；重复调用幂等。"""
    from infra.migrations import upgrade_database

    upgrade_database(engine)


def get_schema_version() -> int:
    """读取数据库中保存的整数架构版本。"""
    from infra.migrations import get_schema_version as _get_schema_version

    return _get_schema_version(engine)
