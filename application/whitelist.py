"""
白名单用户管理应用层（v4 §12）。

- 数据保存于 `whitelist_users` 表，仅业务字段 `user_id`；管理 API 通过本模块复用这些能力。
- `is_whitelisted` 同时识别 `admin_users` 中的用户；管理员用户不复制到白名单表，但同样跳过创建约束。
"""

from __future__ import annotations

from domain.errors import InvalidArgumentError
from infra.db import session_scope
from infra.repositories import AdminUserRepository, WhitelistUserRepository

__all__ = [
    "add_user",
    "remove_user",
    "list_users",
    "is_whitelisted"
]


def add_user(user_id: str) -> bool:
    """新增白名单用户；已存在（含本事务待提交）返回 False。"""
    _validate(user_id)
    with session_scope() as session:
        return WhitelistUserRepository(session).add(user_id)


def remove_user(user_id: str) -> None:
    """删除白名单用户（幂等）。"""
    _validate(user_id)
    with session_scope() as session:
        WhitelistUserRepository(session).delete(user_id)


def list_users() -> list[str]:
    """列出全部白名单用户 ID。"""
    with session_scope() as session:
        return [row.user_id for row in WhitelistUserRepository(session).list_all()]


def is_whitelisted(user_id: str) -> bool:
    """判断用户是否属于有效白名单（显式白名单或管理员清单）。"""
    with session_scope() as session:
        return (
            WhitelistUserRepository(session).exists(user_id)
            or AdminUserRepository(session).exists(user_id)
        )


def _validate(user_id: str) -> None:
    if not user_id or not user_id.strip():
        raise InvalidArgumentError("用户 ID 不能为空")
