"""
Docker Engine 集成层（v4 §7）。

- 使用官方 `docker` SDK 封装，对外只暴露业务需要的能力
（load_image / list_images / tag_image / push_image / remove_image），
不泄露 Engine API 细节。
- 连接方式按平台自动选择，无需环境变量指定（v4 §3.4、§7.1）：Linux/容器内默认
  `/var/run/docker.sock`（compose 挂载），Windows 默认 Docker Desktop 命名管道
  `npipe:////./pipe/docker_engine`；亦可显式传入 `base_url`。
- 失败时 MUST 将底层详细错误写日志，对外只抛合理摘要（v4 §7.5）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import BinaryIO, Optional

# noinspection package-requirements
import docker

from infra.docker.types import LocalImage

logger = logging.getLogger(__name__)

__all__ = [
    "DockerClient",
    "DockerError"
]


class DockerError(Exception):
    """Docker 调用失败时抛出的对外摘要错误（底层细节已写日志）。"""


class DockerClient:
    """Docker Engine 客户端（跨平台：Windows 命名管道 / Linux unix socket）。"""

    def __init__(self, base_url: Optional[str] = None, timeout: int = 300) -> None:
        # base_url=None 时 docker SDK 按平台取默认，与「无环境变量指定」的规范一致。
        # 构造期即探测服务版本，连接不可用也在此收敛为摘要异常。
        try:
            self._client = docker.DockerClient(base_url=base_url, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Docker 客户端初始化失败")
            raise DockerError("无法连接 Docker Engine") from exc

    def load_image(self, stream: BinaryIO) -> list[str]:
        """镜像导入：将上传的镜像 tar 流导入本地 Docker，返回导入镜像的 RepoTag。"""
        try:
            images = self._client.images.load(stream)
        except Exception as exc:  # noqa: BLE001 底层细节写日志，对外只抛摘要
            logger.exception("Docker load 失败")
            raise DockerError("镜像导入到本地 Docker 失败") from exc
        tags: list[str] = []
        for image in images:
            tags.extend(image.tags or [])
        return list(dict.fromkeys(tags))

    def list_images(self) -> list[LocalImage]:
        """列出本地 Docker 镜像（Images 页面的数据来源）。"""
        try:
            images = self._client.images.list()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Docker images.list 失败")
            raise DockerError("获取本地 Docker 镜像列表失败") from exc

        result: list[LocalImage] = []
        for image in images:
            attrs = image.attrs or {}
            short_id = image.short_id
            image_id = short_id if short_id else (image.id or "")
            result.append(
                LocalImage(
                    id=image_id,
                    tags=list(image.tags or []),
                    created=_parse_created(attrs),
                    size=int(attrs.get("Size", 0) or 0),
                )
            )
        return result

    def tag_image(self, source_ref: str, target_ref: str) -> None:
        """为本地镜像创建目标 RepoTag。"""
        try:
            repository, tag = _split_tag(target_ref)
        except ValueError as exc:
            raise DockerError(f"镜像引用非法: {target_ref!r}") from exc
        try:
            image = self._client.images.get(source_ref)
            if not image.tag(repository, tag=tag):
                logger.error("Docker tag 未返回成功: %s -> %s", source_ref, target_ref)
                raise DockerError("镜像重新打标签失败")
        except DockerError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Docker tag 失败: %s -> %s", source_ref, target_ref)
            raise DockerError("镜像重新打标签失败") from exc

    def push_image(self, image_ref: str) -> None:
        """推送镜像至目标 Registry（本地带 tag 的完整引用）。"""
        try:
            repository, tag = _split_tag(image_ref)
        except ValueError as exc:
            raise DockerError(f"镜像引用非法: {image_ref!r}") from exc
        try:
            for line in self._client.images.push(repository, tag=tag, stream=True, decode=True):
                if isinstance(line, dict) and line.get("error"):
                    logger.error("Docker push 出错: %s", line["error"])
                    raise DockerError("镜像推送失败")
        except DockerError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Docker push 失败: %s", image_ref)
            raise DockerError("镜像推送失败") from exc

    def remove_image(self, image_ref: str) -> None:
        """删除本地 Docker 镜像。"""
        try:
            self._client.images.remove(image=image_ref, force=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Docker rmi 失败: %s", image_ref)
            raise DockerError("删除本地镜像失败") from exc


def _split_tag(ref: str) -> tuple[str, str]:
    """将完整镜像引用拆分为 (repository, tag)；无 tag 时默认 latest。非法输入抛 ValueError。

    规则：
    - 空引用（含全空白）→ ValueError。
    - 无冒号 → (ref, "latest")。
    - 冒号后为空（如 `app:`）→ ValueError。
    - 最后一段冒号后含 `/`（registry 端口）→ 视为无 tag → (ref, "latest")。
    - 冒号前为空（如 `:v1`）→ ValueError。
    """
    value = ref.strip()
    if not value:
        raise ValueError("镜像引用不能为空")
    if ":" not in value:
        return value, "latest"
    head, _, tail = value.rpartition(":")
    if not tail:
        raise ValueError(f"镜像引用非法（冒号后缺少 tag）: {ref!r}")
    if "/" in tail:
        return value, "latest"
    if not head:
        raise ValueError(f"镜像引用非法（缺少 repository）: {ref!r}")
    if head.endswith("/"):
        raise ValueError(f"镜像引用非法（repository 缺名称段）: {ref!r}")
    return head, tail


def _parse_created(attrs: dict) -> Optional[datetime]:
    """从镜像 attrs 的 `Created` 解析创建时间；无法解析返回 None。"""
    raw = attrs.get("Created")
    if isinstance(raw, str) and raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None
