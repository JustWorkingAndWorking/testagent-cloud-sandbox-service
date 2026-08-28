"""
Registry HTTP API V2 集成层（v4 §7.4）。

- 已推送判断：`HEAD {registry}/v2/{namespace}/{name}/manifests/{tag}`；
  200 → 已推送（Manifest 存在），404 → 未推送。
- 不比对本地与 Registry 的 Digest，只判断 Manifest 是否存在。
- Registry 无身份验证，直接发送无认证请求（v4 §7.4）。
- 失败时 MUST 将底层详细错误写日志，对外只抛合理摘要（v4 §7.5）。
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit, urlunsplit

import requests

logger = logging.getLogger(__name__)

__all__ = [
    "RegistryClient",
    "RegistryError"
]

#: Manifest 请求的媒体类型（Distribution 规范要求）
_MANIFEST_ACCEPT = (
    "application/vnd.docker.distribution.manifest.v2+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.oci.image.index.v1+json"
)

_CONTAINER_HOST_ALIAS = "host.docker.internal"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class RegistryError(Exception):
    """Registry 调用失败时抛出的对外摘要错误（底层细节已写日志）。"""


class RegistryClient:
    """Registry HTTP API V2 客户端。"""

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def check_image_pushed(self, registry: str, namespace: str, name: str, tag: str) -> bool:
        """判断镜像是否已推送（Manifest 是否存在），不比对 Digest。"""
        url = self._manifest_url(registry, namespace, name, tag)
        try:
            response = requests.head(
                url, timeout=self._timeout, headers={"Accept": _MANIFEST_ACCEPT}
            )
        except requests.RequestException as exc:
            logger.exception("Registry manifest 检查请求失败: %s", url)
            raise RegistryError("Registry 不可达或请求失败") from exc
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        raise RegistryError(f"Registry 返回非预期状态: HTTP {response.status_code}")

    def delete_image(self, registry: str, namespace: str, name: str, tag: str) -> bool:
        """删除 Registry 上的镜像（供镜像删除勾选『同步删除 Registry』使用，v4 §10.6）。

        - 以 HEAD 返回的 `Docker-Content-Digest` 作为删除目标（Distribution 规范）；
          HEAD 缺失时退化为按 tag 删除。
        - 返回 True 表示已删除成功或确认不存在；异常状态返回 False（不抛错），
          由调用方决定提示而非回滚（本地与 Registry 非事务）。
        """
        base = self._manifest_url(registry, namespace, name, tag)
        headers = {"Accept": _MANIFEST_ACCEPT}
        try:
            head = requests.head(base, timeout=self._timeout, headers=headers)
            if head.status_code == 404:
                return True
            digest = head.headers.get("Docker-Content-Digest")
            if digest:
                manifest_base = f"{_normalize_registry(registry)}/v2/{namespace}/{name}/manifests"
                target = f"{manifest_base}/{digest}"
            else:
                target = base
            response = requests.delete(target, timeout=self._timeout)
            return response.status_code in (200, 202, 404)
        except requests.RequestException as exc:
            logger.exception("Registry 删除失败: %s", base)
            raise RegistryError("Registry 删除失败") from exc

    @staticmethod
    def _manifest_url(registry: str, namespace: str, name: str, tag: str) -> str:
        return (
            f"{_normalize_registry(registry)}/v2/"
            f"{namespace}/{name}/manifests/{tag}"
        )


# noinspection HttpUrlsUsage
def _normalize_registry(registry: str) -> str:
    """补全协议并解析容器内的本机 Registry 地址。"""
    value = registry.strip().rstrip("/")
    if value and not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    if os.path.exists("/.dockerenv"):
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        if hostname in _LOOPBACK_HOSTS:
            parsed_port = parsed.port
            port = f":{parsed_port}" if parsed_port is not None else ""
            value = urlunsplit(
                (
                    parsed.scheme,
                    f"{_CONTAINER_HOST_ALIAS}{port}",
                    parsed.path,
                    parsed.query,
                    parsed.fragment,
                )
            )
    return value
