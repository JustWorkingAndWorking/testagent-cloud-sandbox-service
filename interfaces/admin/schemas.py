"""
管理 REST API 请求 / 响应模型。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from config import settings
from interfaces.common import (
    ContainerCreateRequestBase,
    ContainerRuntimeResponse,
    ErrorResponse,
    ExpirationRequest,
    ExpirationResponse,
)

__all__ = [
    "ErrorResponse",
    "ImageReferenceRequest",
    "ImageDeleteRequest",
    "ImageListItem",
    "ImageListResponse",
    "DefaultImageResponse",
    "UserIdRequest",
    "UserIdsResponse",
    "UserMutationResponse",
    "AdminCreateContainerRequest",
    "AdminContainerResponse",
    "AdminContainerListResponse",
    "ExpirationRequest",
    "ExpirationResponse",
    "ContainerLimitRequest",
    "ContainerLimitResponse",
]


class ImageReferenceRequest(BaseModel):
    """镜像注册表相关请求"""

    full_name: str = Field(min_length=1, description="完整镜像名称")


class ImageDeleteRequest(ImageReferenceRequest):
    """删除镜像请求"""

    also_registry: bool = Field(default=True, description="是否同步删除注册表中的镜像")


class ImageListItem(BaseModel):
    """镜像基本属性字段"""

    id: str = Field(description="镜像 ID")
    full_name: str = Field(description="完整镜像名称")
    registry: str = Field(description="镜像注册表")
    namespace: str = Field(description="镜像命名空间")
    name: str = Field(description="镜像名称")
    version: str = Field(description="镜像版本")
    created_at: Optional[str] = Field(default=None, description="镜像创建时间")
    size: int = Field(description="镜像大小，单位为字节")
    status: str = Field(description="镜像状态")


class ImageListResponse(BaseModel):
    """镜像清单响应"""

    images: list[ImageListItem] = Field(description="本地镜像清单")


class DefaultImageResponse(BaseModel):
    """默认镜像响应"""

    full_name: Optional[str] = Field(default=None, description="当前的默认镜像，未设置时为空")


class UserIdRequest(BaseModel):
    """用户清单变更请求"""

    user_id: str = Field(min_length=1, description="用户 ID")


class UserIdsResponse(BaseModel):
    """用户清单响应"""

    user_ids: list[str] = Field(description="用户 ID 列表")


class UserMutationResponse(BaseModel):
    """新增用户清单响应"""

    user_id: str = Field(description="用户 ID")


class AdminCreateContainerRequest(ContainerCreateRequestBase):
    """管理员创建容器请求"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "user_id": "10001",
                    "image": "localhost:5000/testagent/app:v1",
                    "gitee_user": "test_name",
                    "gitee_repository": "test_project",
                    "gitee_branch": "develop",
                    "expiration_hours": settings.container_default_expiration_hours,
                    "authorize_general_account": False,
                    "cpu": 1,
                    "memory": 1,
                }
            ]
        }
    )

    image: Optional[str] = Field(
        default=None,
        min_length=1,
        description="完整镜像名称，省略时将使用默认镜像",
    )
    expiration_hours: Optional[int] = Field(
        default=settings.container_default_expiration_hours,
        ge=0,
        description="容器过期时间，0 表示永不过期",
    )
    cpu: Optional[float] = Field(default=None, gt=0, description="CPU 核数")
    memory: Optional[int] = Field(default=None, gt=0, description="内存大小，单位 Gi")


class AdminContainerResponse(ContainerRuntimeResponse):
    """完整容器信息"""

    image: str = Field(description="完整镜像名称")
    user_id: str = Field(description="用户 ID")
    gitee_user: str = Field(description="码云用户名")
    gitee_repository: str = Field(description="码云仓库")
    gitee_branch: Optional[str] = Field(default=None, description="码云分支，未设置时为空")
    created_at: str = Field(description="容器创建时间")
    expiration_hours: int = Field(description="容器运行时长，单位小时")
    authorize_general_account: bool = Field(description="是否授权通用码云账户登录")
    deleted_at: Optional[str] = Field(default=None, description="业务删除时间，未业务删除时为空")
    business_deleted: bool = Field(description="是否已业务删除")


class AdminContainerListResponse(BaseModel):
    """全部容器信息响应"""

    containers: list[AdminContainerResponse] = Field(description="全部容器完整信息")


class ContainerLimitRequest(BaseModel):
    """容器数量限制变更请求。"""

    container_limit: int = Field(ge=0, description="容器数量上限，0 表示取消数量限制")


class ContainerLimitResponse(BaseModel):
    """容器数量限制响应。"""

    container_count: int = Field(description="当前容器数目")
    container_limit: int = Field(description="当前容器限制")
