"""
REST API 请求 / 响应模型（v4 §14.4~§14.9）。

`POST /containers` 请求包含**必填**字段 `authorize_general_account`（bool），
创建响应中原样返回。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from config import settings

__all__ = [
    "CreateContainerRequest",
    "CreateContainerResponse",
    "ContainerStatusResponse",
    "ContainerIdsResponse",
    "ExpirationRequest",
    "ExpirationResponse",
    "ErrorResponse",
]


class CreateContainerRequest(BaseModel):
    """创建容器请求。

    创建容器时固定使用管理员设置的默认镜像。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "user_id": "10001",
                    "gitee_user": "test_name",
                    "gitee_repository": "test_project",
                    "gitee_branch": "develop",
                    "expiration_hours": settings.container_default_expiration_hours,
                    "authorize_general_account": True,
                }
            ]
        }
    )

    user_id: str = Field(description="用户 ID")
    gitee_user: str = Field(description="码云用户名")
    gitee_repository: str = Field(description="码云仓库")
    gitee_branch: Optional[str] = Field(default=None, description="仓库分支 (可选)")
    expiration_hours: Optional[int] = Field(
        default=settings.container_default_expiration_hours,
    )
    authorize_general_account: bool = Field(description="是否授权通用码云账户")


class CreateContainerResponse(BaseModel):
    """创建容器响应。"""

    container_id: str
    image: str
    status: str
    endpoint: Optional[str] = None
    started_at: Optional[str] = None
    expiration: Optional[str] = None
    authorize_general_account: bool = Field(description="是否授权通用码云账户")


class ContainerStatusResponse(BaseModel):
    """容器状态响应。"""

    container_id: str
    status: str
    endpoint: Optional[str] = None
    started_at: Optional[str] = None
    deleted_at: Optional[str] = None
    remaining_time: Optional[int] = None


class ContainerIdsResponse(BaseModel):
    """容器 ID 查询响应。"""

    container_ids: list[str]


class ExpirationRequest(BaseModel):
    """设置容器运行时长请求。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"expiration_hours": settings.container_default_expiration_hours}]
        }
    )

    expiration_hours: int = Field()


class ExpirationResponse(BaseModel):
    """设置容器运行时长响应。"""

    container_id: str
    expiration_hours: int
    expiration: Optional[str]


class ErrorResponse(BaseModel):
    """错误响应。"""

    code: str
    message: str
