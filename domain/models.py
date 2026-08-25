"""
领域层业务模型（v4 §8.3、§11.1）。

- `ContainerStatus`：业务状态枚举 `pending` / `running` / `stopped` / `business_deleted` / `unknown`。
- `Container`：容器业务模型（含 `authorize_general_account`，变更 #2）。
- 时间均为带时区偏移的 ISO 8601 字符串（v4 §5.3，UTC+8）。
- OpenSandbox 原始运行状态到业务状态的映射规则见 `map_runtime_state`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

__all__ = [
    "ContainerStatus",
    "Container",
    "map_runtime_state"
]


class ContainerStatus(str, Enum):
    """容器业务状态（v4 §8.3、§11.1）。"""

    PENDING = "pending"
    RUNNING = "running"
    STOPPED = "stopped"
    BUSINESS_DELETED = "business_deleted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Container:
    """容器业务模型（v4 §11.1；变更 #2 增加 `authorize_general_account`）。"""

    container_id: str
    image: str

    status: ContainerStatus

    user_id: str
    gitee_user: str
    gitee_repository: str
    gitee_branch: Optional[str]
    authorize_general_account: bool

    created_at: str
    expiration_hours: int
    deleted_at: Optional[str] = None

    @property
    def business_deleted(self) -> bool:
        """是否已业务删除。"""
        return self.deleted_at is not None

    @property
    def expires_at(self) -> Optional[str]:
        """业务删除时刻（`created_at + expiration_hours`，带时区偏移 ISO 8601）；
        `expiration_hours <= 0`（永不过期）返回 None。"""
        return add_hours_to_iso(self.created_at, self.expiration_hours)


def map_runtime_state(raw: Optional[str]) -> ContainerStatus:
    """将 OpenSandbox 原始运行状态映射为业务状态（v4 §8.3）：

    - 运行中 → `running`
    - 已停止（暂停 / 退出 / 终止等）→ `stopped`
    - 创建 / 启动 / 重启尚未稳定等其余状态 → `pending`
    - 获取失败或不可达时应由调用方另行给出 `unknown`（不通过本函数）。
    """
    state = (raw or "").strip().upper()
    if state == "RUNNING":
        return ContainerStatus.RUNNING
    if state in ("PAUSED", "EXITED", "STOPPED", "TERMINATED", "DEAD"):
        return ContainerStatus.STOPPED
    return ContainerStatus.PENDING


def add_hours_to_iso(value: str, hours: int) -> Optional[str]:
    """在带时区偏移的 ISO 时间字符串上累加小时；`hours <= 0` 返回 None（永不过期）。"""
    if hours <= 0:
        return None
    dt = datetime.fromisoformat(value)
    return (dt + timedelta(hours=hours)).isoformat()
