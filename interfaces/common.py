"""
REST 接口公共模型与 OpenAPI 响应描述。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from config import settings

__all__ = [
    "ErrorResponse",
    "ContainerCreateRequestBase",
    "ContainerRuntimeResponse",
    "ExpirationRequest",
    "ExpirationResponse",
    "api_responses",
]


class ErrorResponse(BaseModel):
    """错误响应"""

    code: str = Field(description="错误码")
    message: str = Field(description="错误详情")


class ContainerCreateRequestBase(BaseModel):
    """容器创建参数"""

    user_id: str = Field(description="用户 ID")
    gitee_user: Optional[str] = Field(default=None, description="码云用户名")
    gitee_repository: Optional[str] = Field(default=None, description="码云仓库")
    gitee_branch: Optional[str] = Field(default=None, description="仓库分支 (可选)")
    gitee_url: Optional[str] = Field(default="", description="码云仓库地址前缀")
    authorize_general_account: Optional[bool] = Field(
        default=False,
        description="是否授权通用码云账户登录",
    )


class ContainerRuntimeResponse(BaseModel):
    """容器基本属性字段"""

    container_id: str = Field(description="容器 ID")
    status: str = Field(description="容器状态")
    endpoint: Optional[str] = Field(default=None, description="容器 SSH 访问端点")
    started_at: Optional[str] = Field(default=None, description="容器启动时间")
    expires_at: Optional[str] = Field(default=None, description="容器业务删除时间")


class ExpirationRequest(BaseModel):
    """设置容器过期时间请求"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"expiration_hours": settings.container_default_expiration_hours}]
        }
    )

    expiration_hours: int = Field(
        ge=0,
        description="容器过期时间，0 表示永不过期",
    )


class ExpirationResponse(BaseModel):
    """设置容器过期时间响应"""

    container_id: str = Field(description="容器 ID")
    expires_at: Optional[str] = Field(description="预计业务删除时间，容器永不过期时为空")


_ERROR_LABELS = {
    400: "非法参数 / 配置错误",
    404: "资源不存在",
    409: "业务冲突 / 重复新增",
    500: "服务内部错误",
    502: "外部服务错误",
}


def api_responses(
    success_description: str,
    success_code: int,
    *error_codes: int,
) -> dict[int | str, dict[str, Any]]:
    """生成统一的成功和错误响应文档。"""
    result: dict[int | str, dict[str, Any]] = {
        success_code: {"description": success_description},
        400: {"model": ErrorResponse, "description": "参数校验失败"},
        500: {"model": ErrorResponse, "description": _ERROR_LABELS[500]},
    }
    for code in error_codes:
        result[code] = {
            "model": ErrorResponse,
            "description": _ERROR_LABELS.get(code, "请求失败"),
        }
    return result
