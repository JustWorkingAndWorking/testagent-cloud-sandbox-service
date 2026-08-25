"""
SQLAlchemy Engine / Session 工厂（v4 §6.3）。

- SQLite 文件路径 = `Constants.DB_PATH`（v4 §5.2）。
- 写操作一律在事务中执行：`session_scope` 统一 commit / rollback / close。
- 表创建入口 `init_db()`，由进程入口在启动阶段调用（幂等）。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import Constants

__all__ = [
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
    """建表（幂等）。进程启动时由 main.py 调用。"""
    from infra.orm import Base

    Base.metadata.create_all(engine)
