"""
表定义（v4 §6.2）：`containers`、`settings`、`whitelist_users`、`admin_users`。

注：`containers` 表不设唯一约束；模式/数量限制在应用层（application）校验，
以便白名单用户跳过约束（变更 #3）。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "Base",
    "Container",
    "SettingsRow",
    "WhitelistUserRow",
    "AdminUserRow",
]


class Base(DeclarativeBase):
    """ORM 模型基类。"""


class Container(Base):
    """`containers` 容器业务数据（v4 §6.2.1；变更 #2 增加 `authorize_general_account`）。"""

    __tablename__ = "containers"

    container_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    image: Mapped[str] = mapped_column(String(512))

    user_id: Mapped[str] = mapped_column(String(128))
    gitee_user: Mapped[str] = mapped_column(String(128))
    gitee_repository: Mapped[str] = mapped_column(String(128))
    gitee_branch: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    authorize_general_account: Mapped[bool] = mapped_column(Boolean)

    created_at: Mapped[str] = mapped_column(String(32))
    expiration_hours: Mapped[int] = mapped_column(Integer)
    deleted_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class SettingsRow(Base):
    """`settings` 配置表（v4 §6.2.2）。"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String)


class WhitelistUserRow(Base):
    """`whitelist_users` 白名单用户表（v4 §6.2.3）。"""

    __tablename__ = "whitelist_users"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)


class AdminUserRow(Base):
    """`admin_users` 管理员清单表（变更 #13）。"""

    __tablename__ = "admin_users"

    #: 管理员用户 ID；管理员清单不参与鉴权或其他业务逻辑
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
