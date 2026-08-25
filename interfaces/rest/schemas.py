"""
REST API 请求 / 响应模型（v4 §14.4~§14.9）。

`POST /containers` 请求包含**必填**字段 `authorize_general_account`（bool），
创建响应中原样返回。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from config import settings

__all__ = [
    "CreateContainerRequest",
    "CreateContainerResponse",
    "ContainerStatusResponse",
    "ContainerIdsResponse",
    "ExpirationRequest",
    "ExpirationResponse",
]


class CreateContainerRequest(BaseModel):
    """创建容器请求。

    请求不含镜像信息，固定使用当前配置的默认镜像。
    """

    user_id: str = Field(description="业务用户 ID")
    gitee_user: str = Field(description="Gitee 用户名")
    gitee_repository: str = Field(description="Gitee 仓库名")
    gitee_branch: Optional[str] = Field(default=None, description="Gitee 分支（可选）")
    expiration_hours: Optional[int] = Field(
        default=settings.container_default_expiration_hours,
        description=f"业务删除时长（小时）；默认 {settings.container_default_expiration_hours} 小时（当前配置）；0 表示永不过期",
    )
    authorize_general_account: bool = Field(description="是否授权通用账户（必填）")


class CreateContainerResponse(BaseModel):
    """创建容器响应。"""

    container_id: str
    image: str
    status: str
    endpoint: Optional[str] = None
    started_at: Optional[str] = None
    expiration: Optional[str] = None
    authorize_general_account: bool = Field(description="是否授权通用账户")


class ContainerStatusResponse(BaseModel):
    """容器状态响应。"""

    container_id: str
    status: str
    endpoint: Optional[str] = None
    started_at: Optional[str] = None
    remaining_time: Optional[int] = None


class ContainerIdsResponse(BaseModel):
    """容器 ID 查询响应。"""

    container_ids: list[str]


class ExpirationRequest(BaseModel):
    """设置业务有效时长请求。"""

    expiration_hours: int = Field(description="业务删除时长（小时）；0 表示永不过期")


class ExpirationResponse(BaseModel):
    """设置业务有效时长响应。"""

    container_id: str
    expiration_hours: int
    expiration: Optional[str]