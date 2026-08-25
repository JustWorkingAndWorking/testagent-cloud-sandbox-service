"""
OpenSandbox 集成层数据模型（v4 §8）。

本层透传 SDK 原始运行状态；业务状态映射（`pending` / `running` / `stopped` / `unknown`）
由应用层/Scheduler 完成（v4 §8.3）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "SandboxStatus",
    "SandboxEndpoint",
    "CreatedSandbox"
]


@dataclass(frozen=True)
class SandboxStatus:
    """容器运行状态（SDK 原始语义，v4 §8.3）。"""

    #: 原始状态（如 RUNNING / PENDING / PAUSED / TERMINATED）
    state: str
    reason: Optional[str] = None
    message: Optional[str] = None
    #: 状态最近一次变更时间（ISO 8601，含时区）
    transitioned_at: Optional[str] = None


@dataclass(frozen=True)
class SandboxEndpoint:
    """容器外部访问端点（v4 §8.2 Get Endpoint）。"""

    endpoint: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CreatedSandbox:
    """创建容器返回结果（v4 §11.1）。"""

    container_id: str
