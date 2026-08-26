"""
REST API 容器端点（v4 §14.3~§14.9）。

- 端点薄壳：业务逻辑在应用层（`application.container`），本层仅做参数传递与响应组装。
- 错误由 `AppError` 异常体系抛出，`app.py` 统一映射 HTTP 状态码（v4 §14.10）；
  各端点通过 `responses=` 将可能错误码完整写入 OpenAPI 文档。
- Web 不通过 REST API 调用本服务（v4 §14.1）；本层不提供 Permanent Delete。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Response

from application import container
from domain.models import add_hours_to_iso
from interfaces.rest.schemas import (
    ContainerIdsResponse,
    ContainerStatusResponse,
    CreateContainerRequest,
    CreateContainerResponse,
    ErrorResponse,
    ExpirationRequest,
    ExpirationResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/containers", tags=["containers"])

#: 业务错误码 → 文档说明（v4 §14.10）
_ERROR_LABELS = {
    400: "参数非法 / 未配置默认镜像",
    404: "容器不存在",
    409: "业务限制",
    500: "服务内部错误",
    502: "外部服务错误",
}


def _responses(ok_description: str, ok_code: int, *codes: int) -> dict[int | str, dict[str, Any]]:
    """生成 OpenAPI responses：成功描述（200/204）+ 全量错误码（400/404/409/500/502/422）及中文说明。"""
    result: dict[int | str, dict[str, Any]] = {
        ok_code: {"description": ok_description},
        422: {"model": ErrorResponse, "description": "参数校验失败"},
        500: {"model": ErrorResponse, "description": _ERROR_LABELS[500]},
    }
    for code in codes:
        result[code] = {
            "model": ErrorResponse,
            "description": _ERROR_LABELS.get(code, "错误"),
        }
    return result


@router.post(
    "",
    response_model=CreateContainerResponse,
    status_code=200,
    openapi_extra={"requestBody": {"description": "创建容器参数；固定使用管理员配置的镜像。"}},
    responses=_responses("成功", 200, 400, 409, 502),
)
def create_container(request: CreateContainerRequest) -> CreateContainerResponse:
    """创建并启动容器，固定使用管理员配置的镜像。"""
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
    # 创建成功后实时查询一次端点与启动时间，随响应返回；查询失败回退空值不阻断创建
    endpoint: Optional[str] = None
    started_at: Optional[str] = None
    try:
        view = container.get_status(created.container_id)
        endpoint = view.endpoint
        started_at = view.started_at
    except Exception:  # noqa: BLE001
        logger.warning("创建后实时查询端点/启动时间失败: %s", created.container_id)
    return CreateContainerResponse(
        container_id=created.container_id,
        image=created.image,
        status=created.status.value,
        endpoint=endpoint,
        started_at=started_at,
        expiration=add_hours_to_iso(created.created_at, created.expiration_hours),
        authorize_general_account=created.authorize_general_account,
    )


@router.get("", response_model=ContainerIdsResponse, responses=_responses("成功", 200, 400))
def query_container_ids(
    user_id: str,
    gitee_user: Optional[str] = None,
    gitee_repository: Optional[str] = None,
    gitee_branch: Optional[str] = None,
) -> ContainerIdsResponse:
    """按业务条件查询容器 ID。"""
    ids = container.query_container_ids(
        user_id=user_id,
        gitee_user=gitee_user,
        gitee_repository=gitee_repository,
        gitee_branch=gitee_branch,
    )
    return ContainerIdsResponse(container_ids=ids)


@router.get("/{container_id}", response_model=ContainerStatusResponse, responses=_responses("成功", 200, 404))
def get_container_status(container_id: str) -> ContainerStatusResponse:
    """查询指定容器运行状态。"""
    view = container.get_status(container_id)
    return ContainerStatusResponse(
        container_id=view.container_id,
        status=view.status.value,
        endpoint=view.endpoint,
        started_at=view.started_at,
        remaining_time=view.remaining_time,
    )


@router.post("/{container_id}/start", status_code=204, responses=_responses("成功 (无内容)", 204, 404))
def start(container_id: str) -> Response:
    """启动指定容器。"""
    container.start(container_id)
    return Response(status_code=204)


@router.post("/{container_id}/stop", status_code=204, responses=_responses("成功 (无内容)", 204, 404))
def stop(container_id: str) -> Response:
    """停止指定容器。"""
    container.stop(container_id)
    return Response(status_code=204)


@router.post("/{container_id}/kill", status_code=204, responses=_responses("成功 (无内容)", 204, 404))
def kill(container_id: str) -> Response:
    """强制停止指定容器。"""
    container.kill(container_id)
    return Response(status_code=204)


@router.post("/{container_id}/restart", status_code=204, responses=_responses("成功 (无内容)", 204, 404))
def restart(container_id: str) -> Response:
    """重启指定容器。"""
    container.restart(container_id)
    return Response(status_code=204)


@router.post(
    "/{container_id}/expiration",
    response_model=ExpirationResponse,
    openapi_extra={"requestBody": {"description": "新的容器运行时长 (小时)。"}},
    responses=_responses("成功", 200, 400, 404),
)
def set_expiration(container_id: str, request: ExpirationRequest) -> ExpirationResponse:
    """设置指定容器运行时长 (0 表示一直运行)。"""
    view = container.set_expiration(container_id, request.expiration_hours)
    return ExpirationResponse(
        container_id=view.container_id,
        expiration_hours=view.expiration_hours,
        expiration=view.expiration,
    )


@router.post("/{container_id}/delete", status_code=204, responses=_responses("成功 (无内容)", 204, 404))
def delete(container_id: str) -> Response:
    """删除指定容器。"""
    container.business_delete(container_id)
    return Response(status_code=204)
