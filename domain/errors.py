"""
领域层业务异常（v4 §14.10 错误码映射基础）。

应用层 / 接口层按 `http_status` 与 `code` 对外输出；底层详细错误由 infra 层记录并转换摘要。
"""

from __future__ import annotations

from typing import Optional

__all__ = [
    "AppError",
    "InvalidArgumentError",
    "DefaultImageNotConfiguredError",
    "ContainerNotFoundError",
    "UserNotFoundError",
    "BusinessConflictError",
    "LimitReachedError",
    "ExternalDependencyError",
]


class AppError(Exception):
    """业务异常基类（可携带对外 HTTP 状态码与业务码）。"""

    #: 对外 HTTP 状态码（v4 §14.10）
    http_status: int = 500
    #: 业务码
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        http_status: Optional[int] = None,
        code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if http_status is not None:
            self.http_status = http_status
        if code is not None:
            self.code = code


class InvalidArgumentError(AppError):
    """参数缺失或非法（HTTP 400）。"""

    http_status = 400
    code = "invalid_argument"


class DefaultImageNotConfiguredError(InvalidArgumentError):
    """未配置默认镜像（HTTP 400，v4 §10.5 / §14.4）。"""

    code = "default_image_not_configured"


class ContainerNotFoundError(AppError):
    """容器不存在（含业务已删除 / 已物理删除，HTTP 404，v4 §14.5/§14.7）。"""

    http_status = 404
    code = "container_not_found"


class UserNotFoundError(AppError):
    """用户清单中不存在指定用户（HTTP 404）。"""

    http_status = 404
    code = "user_not_found"


class BusinessConflictError(AppError):
    """业务冲突（HTTP 409，v4 §14.10）。"""

    http_status = 409
    code = "conflict"


class LimitReachedError(BusinessConflictError):
    """超过创建限制（模式限制 / 容器数量限制，HTTP 409，v4 §11.2）。"""

    code = "limit_reached"


class ExternalDependencyError(AppError):
    """外部依赖（OpenSandbox / Docker / Registry）错误（HTTP 502，v4 §14.10）。"""

    http_status = 502
    code = "external_dependency"
