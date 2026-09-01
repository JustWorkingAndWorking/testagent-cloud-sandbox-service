"""
用户 REST API 请求 / 响应模型。

`POST /user/containers` 请求包含可选字段 `authorize_general_account`（bool，省略时不授权），并支持可选的
Gitee 信息；容器过期时间和资源限制由服务端管理。用户容器详情还返回 Gitee 用户和仓库信息。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from interfaces.common import (
    ContainerCreateRequestBase,
    ContainerRuntimeResponse,
    ErrorResponse,
)

__all__ = [
    "CreateContainerRequest",
    "CreateContainerResponse",
    "ContainerStatusResponse",
    "ContainerIdsResponse",
    "AdminCheckRequest",
    "AdminCheckResponse",
    "ErrorResponse",
]


class CreateContainerRequest(ContainerCreateRequestBase):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "user_id": "10001",
                    "gitee_user": "test_name",
                    "gitee_repository": "test_project",
                    "gitee_branch": "develop",
                    "authorize_general_account": True,
                }
            ]
        }
    )


class CreateContainerResponse(ContainerRuntimeResponse):
    ...


class ContainerStatusResponse(ContainerRuntimeResponse):
    gitee_user: str = Field(description="容器所属的码云用户名")
    gitee_repository: str = Field(description="容器所属的码云仓库")


class ContainerIdsResponse(BaseModel):
    container_ids: list[str] = Field(description="容器 ID 列表")


class AdminCheckRequest(BaseModel):
    """查询用户管理员身份请求"""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, description="用户 ID")


class AdminCheckResponse(BaseModel):
    """用户管理员身份查询响应"""

    admin: bool = Field(description="是否为管理员")
