"""
REST API 应用装配（v4 §14）。

- 错误映射（T8.3）：400（参数非法/未配置默认镜像，含 Pydantic 校验）、404、409、500、502。
- 文档访问控制（T8.4）：`/docs`、`/redoc`、`/openapi.json` 需 Basic 登录
  （凭据来自 `TA_SS_REST_API_USERNAME` / `TA_SS_REST_API_PASSWORD`）；REST 业务端点不认证。
- 底层错误 MUST 写日志（v4 §14.10），对外只返回合理摘要。
"""

from __future__ import annotations

import base64
import logging
import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from config import settings
from domain.errors import AppError
from interfaces.admin.containers import router as admin_container_router
from interfaces.admin.images import router as admin_image_router
from interfaces.admin.state import router as admin_state_router
from interfaces.admin.users import router as admin_user_router
from interfaces.user.containers import router as user_container_router
from interfaces.user.users import router as user_user_router

logger = logging.getLogger(__name__)

__all__ = [
    "create_app"
]

_DOC_PATHS = {"/docs", "/redoc", "/openapi.json"}


def create_app() -> FastAPI:
    """构建 FastAPI 应用（REST 业务端点 + 错误映射 + 文档登录保护）。"""
    app = FastAPI(title="TestAgent Cloud Sandbox Service", version="1.0.0")
    app.include_router(user_container_router)
    app.include_router(user_user_router)
    app.include_router(admin_image_router)
    app.include_router(admin_user_router)
    app.include_router(admin_state_router)
    app.include_router(admin_container_router)

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        # 底层错误已在 infra/application 层记录；此处记录请求上下文与业务码
        if exc.http_status >= 500:
            logger.error(
                "REST %s %s -> HTTP %s [%s] %s",
                request.method, request.url.path, exc.http_status, exc.code, exc.message,
            )
        else:
            logger.warning(
                "REST %s %s -> HTTP %s [%s] %s",
                request.method, request.url.path, exc.http_status, exc.code, exc.message,
            )
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        logger.warning(
            "REST 参数校验失败 %s %s: %s",
            request.method,
            request.url.path,
            [str(e) for e in exc.errors()],
        )
        return JSONResponse(
            status_code=400,
            content={"code": "invalid_argument", "message": "请求参数非法"},
        )

    @app.exception_handler(Exception)
    async def _internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        _ = exc
        logger.exception("REST 内部错误: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "服务内部错误"},
        )

    @app.middleware("http")
    async def _docs_auth(request: Request, call_next):
        if request.url.path in _DOC_PATHS and not _authorized(request):
            return JSONResponse(
                status_code=401,
                content={"code": "unauthorized", "message": "未认证"},
                headers={"WWW-Authenticate": 'Basic realm="docs"'},
            )
        return await call_next(request)

    @app.middleware("http")
    async def _server_header(request: Request, call_next):
        response = await call_next(request)
        response.headers["server"] = "testagent-cloud"
        return response

    def _custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        # RequestValidationError is deliberately mapped to HTTP 400 by the
        # application handler; remove FastAPI's generated 422 contract.
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation.get("responses", {}).pop("422", None)
        app.openapi_schema = schema
        return schema

    app.openapi = _custom_openapi  # type: ignore[method-assign]

    return app


def _authorized(request: Request) -> bool:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False
    # noinspection broad-exception
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
        username, _, password = decoded.partition(":")
    except Exception:  # noqa: BLE001
        return False
    return secrets.compare_digest(username, settings.rest_api_username) and secrets.compare_digest(
        password, settings.rest_api_password
    )
