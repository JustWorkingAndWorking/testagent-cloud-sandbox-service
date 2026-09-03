"""
Alembic 运行环境配置。

应用启动时由 `infra.migrations` 注入与 SQLAlchemy Engine 一致的数据库 URL；
直接使用 Alembic CLI 时则使用 `alembic.ini` 中的 URL。
"""

from __future__ import annotations

from typing import Literal, cast

# noinspection package-requirements
from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection
from sqlalchemy.schema import SchemaItem

from infra.orm import Base

__all__ = []

config = context.config
target_metadata = Base.metadata


# noinspection unused-parameter
def _include_object(
    object_: SchemaItem,
    name: str | None,
    object_type: Literal[
        "schema",
        "table",
        "column",
        "index",
        "unique_constraint",
        "foreign_key_constraint",
        "check_constraint",
    ],
    reflected: bool,
    compare_to: SchemaItem | None,
) -> bool:
    """迁移元数据表由 Alembic/本服务共同维护，不参与业务表差异生成。"""
    if object_type == "table" and name == "alembic_version":
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        _run_migrations(cast(Connection, supplied_connection))
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        with connectable.connect() as connection:
            _run_migrations(connection)
    finally:
        connectable.dispose()


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
