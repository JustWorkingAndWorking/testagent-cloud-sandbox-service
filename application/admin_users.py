"""
管理员清单管理应用层（变更 #13）。

- 数据保存于 `admin_users` 表，仅业务字段 `user_id`。
- 本模块只提供清单 CRUD，不负责 API 鉴权；管理员用户的有效白名单语义由
  `application.whitelist.is_whitelisted` 统一判断。
"""

from __future__ import annotations

from domain.errors import InvalidArgumentError, UserNotFoundError
from infra.db import session_scope
from infra.repositories import AdminUserRepository

__all__ = [
    "add_user",
    "remove_user",
    "list_users",
]


def add_user(user_id: str) -> bool:
    """新增管理员用户；已存在（含本事务待提交）返回 False。"""
    _validate(user_id)
    with session_scope() as session:
        return AdminUserRepository(session).add(user_id)


def remove_user(user_id: str) -> None:
    """删除管理员用户；用户不存在时抛出 404。"""
    _validate(user_id)
    with session_scope() as session:
        repo = AdminUserRepository(session)
        if not repo.exists(user_id):
            raise UserNotFoundError("用户不存在")
        repo.delete(user_id)


def list_users() -> list[str]:
    """列出全部管理员用户 ID。"""
    with session_scope() as session:
        return [row.user_id for row in AdminUserRepository(session).list_all()]


def _validate(user_id: str) -> None:
    if not user_id or not user_id.strip():
        raise InvalidArgumentError("用户 ID 不能为空")
