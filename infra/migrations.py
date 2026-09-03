"""
Alembic 数据库迁移编排。

Alembic 的 `alembic_version.version_num` 用于记录迁移脚本标识，类型由
Alembic 固定为字符串；`schema_version.version` 是本服务额外维护的 INTEGER
架构版本，二者都必须随迁移成功同步更新。
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import cast

# noinspection package-requirements
from alembic import command
# noinspection package-requirements
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from config import Constants

__all__ = [
    "get_schema_version",
    "upgrade_database",
]

_PROJECT_ROOT = Path(Constants.APP_ROOT_PATH.value)
_ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"
_MIGRATIONS_PATH = _PROJECT_ROOT / "migrations"
_migration_lock = Lock()


def _alembic_config(engine: Engine) -> Config:
    config = Config(str(_ALEMBIC_INI))
    # Always use the same URL as the application engine, not a value copied from
    # an environment-specific alembic.ini file.
    config.set_main_option("script_location", str(_MIGRATIONS_PATH))
    config.set_main_option("prepend_sys_path", str(_PROJECT_ROOT))
    config.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False))
    return config


def upgrade_database(engine: Engine) -> None:
    """在进程内串行执行全部待处理迁移。"""
    with _migration_lock:
        with engine.begin() as connection:
            config = _alembic_config(engine)
            # Reuse the application connection. This also keeps SQLite in-memory
            # databases usable in tests and makes version synchronization atomic.
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            _synchronize_schema_version(connection)


def _synchronize_schema_version(connection: Connection) -> None:
    """将已提交的 Alembic 数字 revision 写入 INTEGER 版本表。"""
    revisions = list(
        connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
    )
    if not revisions:
        return
    if len(revisions) != 1:
        raise RuntimeError("数据库存在多个 Alembic 迁移头，无法映射为单一整数版本")
    try:
        version = int(revisions[0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Alembic revision 必须使用整数版本号: {revisions[0]!r}"
        ) from exc
    result = connection.execute(
        text("UPDATE schema_version SET version = :version WHERE id = 1"),
        {"version": version},
    )
    if result.rowcount != 1:
        raise RuntimeError("数据库缺少 schema_version 初始版本记录")


def get_schema_version(engine: Engine) -> int:
    """读取 `schema_version` 中的 INTEGER 版本值。"""
    with engine.connect() as connection:
        value = cast(
            int | str | None,
            connection.execute(
                text("SELECT version FROM schema_version WHERE id = 1")
            ).scalar_one_or_none(),
        )
    if value is None:
        # 缺少版本记录不是合法版本，不能继续进行 int(None) 转换。
        raise RuntimeError("数据库缺少 schema_version 版本记录")
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"数据库架构版本非法: {value!r}") from exc
    if version < 1:
        raise RuntimeError(f"数据库架构版本非法: {value!r}")
    return version
