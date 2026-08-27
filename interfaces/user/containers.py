"""
用户 REST API 容器端点。

- 端点薄壳：业务逻辑在应用层（`application.container`），本层仅做参数传递与响应组装。
- 错误由 `AppError` 异常体系抛出，`app.py` 统一映射 HTTP 状态码（v4 §14.10）；
  各端点通过 `responses=` 将可能错误码完整写入 OpenAPI 文档。
- 用户 REST API 不提供 Permanent Delete；该能力仅由管理 API 提供。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Response

from application import container
from domain.models import add_hours_to_iso
from interfaces.common import api_responses
from interfaces.user.schemas import (
    ContainerIdsResponse,
    ContainerStatusResponse,
    CreateContainerRequest,
    CreateContainerResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user/containers", tags=["用户 API"])


@router.post(
    "",
    response_model=CreateContainerResponse,
    status_code=200,
    responses=api_responses("成功", 200, 400, 409, 502),
)
def create_container(request: CreateContainerRequest) -> CreateContainerResponse:
    """创建并启动容器，固定使用管理员配置的镜像。"""
    created = container.create_container(
        container.CreateContainerParams(
            user_id=request.user_id,
            gitee_user=request.gitee_user,
            gitee_repository=request.gitee_repository,
            gitee_branch=request.gitee_branch,
            image=None,
            authorize_general_account=request.authorize_general_account,
        )
    )
    # 创建成功后实时查询一次端点与启动时间，随响应返回；查询失败回退空值不阻断创建
    endpoint: Optional[str] = None
    started_at: Optional[str] = None
    # noinspection broad-exception
    try:
        view = container.get_status(created.container_id)
        endpoint = view.endpoint
        started_at = view.started_at
    except Exception:  # noqa: BLE001
        logger.warning("创建后实时查询端点/启动时间失败: %s", created.container_id)
    return CreateContainerResponse(
        container_id=created.container_id,
        status=created.status.value,
        endpoint=endpoint,
        started_at=started_at,
        expires_at=add_hours_to_iso(created.created_at, created.expiration_hours),
    )


@router.get("", response_model=ContainerIdsResponse, responses=api_responses("成功", 200, 400))
def query_container_ids(
    user_id: str,
    gitee_user: Optional[str] = None,
    gitee_repository: Optional[str] = None,
    gitee_branch: Optional[str] = None,
) -> ContainerIdsResponse:
    """按条件查询容器 ID。"""
    ids = container.query_container_ids(
        user_id=user_id,
        gitee_user=gitee_user,
        gitee_repository=gitee_repository,
        gitee_branch=gitee_branch,
    )
    return ContainerIdsResponse(container_ids=ids)


@router.get("/{container_id}", response_model=ContainerStatusResponse, responses=api_responses("成功", 200, 404))
def get_container_status(container_id: str) -> ContainerStatusResponse:
    """查询指定容器运行状态。"""
    view = container.get_status(container_id)
    return ContainerStatusResponse(
        container_id=view.container_id,
        status=view.status.value,
        endpoint=view.endpoint,
        started_at=view.started_at,
        expires_at=view.expires_at,
    )


@router.post("/{container_id}/start", status_code=204, responses=api_responses("成功 (无内容)", 204, 404))
def start(container_id: str) -> Response:
    """启动指定容器。"""
    container.start(container_id)
    return Response(status_code=204)


@router.post("/{container_id}/stop", status_code=204, responses=api_responses("成功 (无内容)", 204, 404))
def stop(container_id: str) -> Response:
    """停止指定容器。"""
    container.stop(container_id)
    return Response(status_code=204)


@router.post("/{container_id}/restart", status_code=204, responses=api_responses("成功 (无内容)", 204, 404))
def restart(container_id: str) -> Response:
    """重启指定容器。"""
    container.restart(container_id)
    return Response(status_code=204)


@router.post("/{container_id}/delete", status_code=204, responses=api_responses("成功 (无内容)", 204, 404))
def delete(container_id: str) -> Response:
    """删除指定容器。"""
    container.business_delete(container_id)
    return Response(status_code=204)
