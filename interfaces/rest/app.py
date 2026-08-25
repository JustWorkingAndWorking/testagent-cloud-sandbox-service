"""
REST API 应用装配（v4 §14）。

- 错误映射（T8.3）：400（参数非法/未配置默认镜像，含 Pydantic 校验）、404、409、500、502。
- 文档访问控制（T8.4）：`/docs`、`/redoc`、`/openapi.json` 需 Basic 登录
  （凭据 `TA_SS_WEB_USERNAME` / `TA_SS_WEB_PASSWORD`）；REST 业务端点不认证。
- 底层错误 MUST 写日志（v4 §14.10），对外只返回合理摘要。
"""

from __future__ import annotations

import base64
import logging
import secrets

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from config import settings
from domain.errors import AppError
from interfaces.rest import containers

logger = logging.getLogger(__name__)

__all__ = [
    "create_app"
]

_DOC_PATHS = {"/docs", "/redoc", "/openapi.json"}


def create_app() -> FastAPI:
    """构建 FastAPI 应用（REST 业务端点 + 错误映射 + 文档登录保护）。"""
    app = FastAPI(title="TestAgent Cloud Sandbox Service", version="1.0.0")
    app.include_router(containers.router)

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        _ = request
        # 底层错误已在 infra/application 层记录；此处仅返回对外摘要
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        _ = request, exc
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
                content={"code": "unauthorized", "message": "Unauthorized"},
                headers={"WWW-Authenticate": 'Basic realm="docs"'},
            )
        return await call_next(request)

    return app


def _authorized(request: Request) -> bool:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
        username, _, password = decoded.partition(":")
    except Exception:  # noqa: BLE001
        return False
    return secrets.compare_digest(username, settings.web_username) and secrets.compare_digest(
        password, settings.web_password
    )
