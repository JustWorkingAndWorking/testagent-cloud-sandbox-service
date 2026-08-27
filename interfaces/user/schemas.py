"""
用户 REST API 请求 / 响应模型。

`POST /user/containers` 请求包含可选字段 `authorize_general_account`（bool，省略时不授权），并支持可选的
Gitee 信息；容器过期时间和资源限制由服务端管理。
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
    ...


class ContainerIdsResponse(BaseModel):
    container_ids: list[str] = Field(description="容器 ID 列表")
