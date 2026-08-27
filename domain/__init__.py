"""
领域层（v4 §4.3）：业务模型与业务异常。

公共模块为 `models`（业务模型与状态）与 `errors`（业务异常）。
"""

from __future__ import annotations

from domain.errors import (
    AppError,
    BusinessConflictError,
    ContainerNotFoundError,
    DefaultImageNotConfiguredError,
    ExternalDependencyError,
    InvalidArgumentError,
    LimitReachedError,
    UserNotFoundError,
)
from domain.models import Container, ContainerStatus, map_runtime_state

__all__ = [
    "AppError",
    "InvalidArgumentError",
    "DefaultImageNotConfiguredError",
    "ContainerNotFoundError",
    "UserNotFoundError",
    "BusinessConflictError",
    "LimitReachedError",
    "ExternalDependencyError",
    "Container",
    "ContainerStatus",
    "map_runtime_state",
]
