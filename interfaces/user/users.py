"""
用户 REST API 用户信息端点。
"""

from __future__ import annotations

from fastapi import APIRouter

from application import admin_users
from interfaces.common import api_responses
from interfaces.user.schemas import AdminCheckRequest, AdminCheckResponse

__all__ = [
    "router",
]


router = APIRouter(prefix="/user", tags=["用户 API"])


@router.post(
    "/check",
    response_model=AdminCheckResponse,
    status_code=200,
    responses=api_responses("成功", 200, 400),
)
def check_admin(request: AdminCheckRequest) -> AdminCheckResponse:
    """查询指定用户是否为管理员。"""
    return AdminCheckResponse(admin=admin_users.is_admin(request.user_id))
