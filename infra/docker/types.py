"""
Docker 集成层数据模型（v4 §7.3）。

一行 = 一个可操作镜像，由 Docker 返回的 RepoTag 解析得出；元数据取自 Docker 返回结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

__all__ = [
    "LocalImage"
]


@dataclass(frozen=True)
class LocalImage:
    """本地 Docker 镜像。"""

    #: Image ID（短 12 位，用于展示）
    id: str
    #: RepoTag 列表（原样解析与展示，不区分是否携带 registry 前缀）
    tags: list[str] = field(default_factory=list)
    #: 镜像创建时间
    created: Optional[datetime] = None
    #: 镜像大小（字节）
    size: int = 0
