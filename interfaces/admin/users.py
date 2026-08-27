"""
管理端白名单和管理员清单 API。
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from application import admin_users, whitelist
from domain.errors import BusinessConflictError
from interfaces.admin.schemas import UserIdRequest, UserIdsResponse, UserMutationResponse
from interfaces.common import api_responses

__all__ = [
    "router",
]


router = APIRouter(prefix="/admin", tags=["管理员 API (用户清单操作)"])


# 白名单接口顺序：增加、获取、删除
@router.post(
    "/whitelist-users",
    response_model=UserMutationResponse,
    status_code=200,
    responses=api_responses("成功", 200, 409),
)
def add_whitelist_user(request: UserIdRequest) -> UserMutationResponse:
    """新增白名单用户。"""
    if not whitelist.add_user(request.user_id):
        raise BusinessConflictError("用户已在白名单中")
    return UserMutationResponse(user_id=request.user_id)


@router.get(
    "/whitelist-users",
    response_model=UserIdsResponse,
    responses=api_responses("成功", 200),
)
def list_whitelist_users() -> UserIdsResponse:
    """获取白名单用户。"""
    return UserIdsResponse(user_ids=whitelist.list_users())


@router.post(
    "/whitelist-users/delete",
    status_code=204,
    responses=api_responses("成功 (无内容)", 204),
)
def delete_whitelist_user(request: UserIdRequest) -> Response:
    """删除白名单用户。"""
    whitelist.remove_user(request.user_id)
    return Response(status_code=204)


# 管理员清单接口顺序：增加、获取、删除
@router.post(
    "/admin-users",
    response_model=UserMutationResponse,
    status_code=200,
    responses=api_responses("成功", 200, 409),
)
def add_admin_user(request: UserIdRequest) -> UserMutationResponse:
    """新增管理员清单用户。"""
    if not admin_users.add_user(request.user_id):
        raise BusinessConflictError("用户已在管理员清单中")
    return UserMutationResponse(user_id=request.user_id)


@router.get(
    "/admin-users",
    response_model=UserIdsResponse,
    responses=api_responses("成功", 200),
)
def list_admin_users() -> UserIdsResponse:
    """获取管理员清单。"""
    return UserIdsResponse(user_ids=admin_users.list_users())


@router.post(
    "/admin-users/delete",
    status_code=204,
    responses=api_responses("成功 (无内容)", 204),
)
def delete_admin_user(request: UserIdRequest) -> Response:
    """删除管理员清单用户。"""
    admin_users.remove_user(request.user_id)
    return Response(status_code=204)
