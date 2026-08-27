"""
用户端与管理端共用的容器生命周期路由。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Response

from application import container as container_service
from interfaces.common import api_responses

__all__ = [
    "register_container_action_routes",
]


def register_container_action_routes(
    router: APIRouter,
    *,
    operation_id_prefix: str,
) -> None:
    """向指定 Router 注册用户端和管理端共用的四个容器动作。"""

    _register_action(
        router,
        action=container_service.start,
        path="/{container_id}/start",
        operation_id=f"{operation_id_prefix}_start_container",
        summary="启动指定容器。",
    )
    _register_action(
        router,
        action=container_service.stop,
        path="/{container_id}/stop",
        operation_id=f"{operation_id_prefix}_stop_container",
        summary="停止指定容器。",
    )
    _register_action(
        router,
        action=container_service.restart,
        path="/{container_id}/restart",
        operation_id=f"{operation_id_prefix}_restart_container",
        summary="重启指定容器。",
    )
    _register_action(
        router,
        action=container_service.business_delete,
        path="/{container_id}/delete",
        operation_id=f"{operation_id_prefix}_delete_container",
        summary="删除指定容器。",
    )


def _register_action(
    router: APIRouter,
    *,
    action: Callable[[str], None],
    path: str,
    operation_id: str,
    summary: str,
) -> None:
    @router.post(
        path,
        status_code=204,
        operation_id=operation_id,
        name=operation_id,
        summary=summary,
        responses=api_responses("成功 (无内容)", 204, 404, 502),
    )
    def action_endpoint(container_id: str) -> Response:
        action(container_id)
        return Response(status_code=204)
