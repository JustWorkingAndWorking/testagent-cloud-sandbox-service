"""
调度服务（v4 §13）。

公共模块为 `lifecycle`：独立检查（过期/保留期/状态刷新）、补偿与后台循环。
"""

from __future__ import annotations

__all__ = [
    "lifecycle"
]
