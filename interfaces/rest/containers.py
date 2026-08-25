"""
REST API 容器端点（v4 §14.3~§14.9）。

- 端点薄壳：业务逻辑在应用层（`application.container`），本层仅做参数传递与响应组装。
- 错误由 `AppError` 异常体系抛出，`app.py` 统一映射 HTTP 状态码（v4 §14.10）。
- Web 不通过 REST API 调用本服务（v4 §14.1）；本层不提供 Permanent Delete。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Response

from application import container
from domain.models import add_hours_to_iso
from interfaces.rest.schemas import (
    ContainerIdsResponse,
    ContainerStatusResponse,
    CreateContainerRequest,
    CreateContainerResponse,
    ExpirationRequest,
    ExpirationResponse,
)

router = APIRouter(prefix="/containers", tags=["containers"])


@router.post("", response_model=CreateContainerResponse, status_code=200)
def create_container(request: CreateContainerRequest) -> CreateContainerResponse:
    """创建并自动启动容器（固定使用当前配置的默认镜像）。"""
    created = container.create_container(
        container.CreateContainerParams(
            user_id=request.user_id,
            gitee_user=request.gitee_user,
            gitee_repository=request.gitee_repository,
            gitee_branch=request.gitee_branch,
            expiration_hours=request.expiration_hours,
            image=None,
            authorize_general_account=request.authorize_general_account,
        )
    )
    return CreateContainerResponse(
        container_id=created.container_id,
        image=created.image,
        status=created.status.value,
        endpoint=None,
        started_at=None,
        expiration=add_hours_to_iso(created.created_at, created.expiration_hours),
        authorize_general_account=created.authorize_general_account,
    )


@router.get("", response_model=ContainerIdsResponse)
def query_container_ids(
    user_id: Optional[str] = None,
    gitee_user: Optional[str] = None,
    gitee_repository: Optional[str] = None,
    gitee_branch: Optional[str] = None,
) -> ContainerIdsResponse:
    """按业务条件查询容器 ID（AND 组合，不含业务已删除）。"""
    ids = container.query_container_ids(
        user_id=user_id,
        gitee_user=gitee_user,
        gitee_repository=gitee_repository,
        gitee_branch=gitee_branch,
    )
    return ContainerIdsResponse(container_ids=ids)


@router.get("/{container_id}", response_model=ContainerStatusResponse)
def get_container_status(container_id: str) -> ContainerStatusResponse:
    """查询容器运行状态。"""
    view = container.get_status(container_id)
    return ContainerStatusResponse(
        container_id=view.container_id,
        status=view.status.value,
        endpoint=view.endpoint,
        started_at=view.started_at,
        remaining_time=view.remaining_time,
    )


@router.post("/{container_id}/start", status_code=204)
def start(container_id: str) -> Response:
    """启动容器（已运行重复调用幂等成功）。"""
    container.start(container_id)
    return Response(status_code=204)


@router.post("/{container_id}/stop", status_code=204)
def stop(container_id: str) -> Response:
    """停止容器（已停止重复调用幂等成功）。"""
    container.stop(container_id)
    return Response(status_code=204)


@router.post("/{container_id}/kill", status_code=204)
def kill(container_id: str) -> Response:
    """强制终止容器（已停止重复调用幂等成功）。"""
    container.kill(container_id)
    return Response(status_code=204)


@router.post("/{container_id}/restart", status_code=204)
def restart(container_id: str) -> Response:
    """重启容器（Container ID 不变）。"""
    container.restart(container_id)
    return Response(status_code=204)


@router.post("/{container_id}/expiration", response_model=ExpirationResponse)
def set_expiration(container_id: str, request: ExpirationRequest) -> ExpirationResponse:
    """设置业务有效时长（仅修改时长，不重置创建时间；0 表示永不过期）。"""
    view = container.set_expiration(container_id, request.expiration_hours)
    return ExpirationResponse(
        container_id=view.container_id,
        expiration_hours=view.expiration_hours,
        expiration=view.expiration,
    )


@router.post("/{container_id}/delete", status_code=204)
def business_delete(container_id: str) -> Response:
    """业务删除容器（停止并进入保留期；已业务删除重复调用幂等成功）。"""
    container.business_delete(container_id)
    return Response(status_code=204)
