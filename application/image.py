"""
镜像管理应用层（v4 §10）。

- 后端业务逻辑集中于此：镜像列表与可用性、上传（load）、推送（Push）、默认镜像管理、删除；
  REST 与 Web 仅承担必要输入/输出与交互，不重复业务判断。
- 数据源：本地 Docker（`infra.docker`）+ Registry（`infra.registry`）+ settings（`default_image`）。
- 失败时：底层详细错误由 infra 层写日志，本层转换为对外业务异常（v4 §14.10）。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from config import Constants, settings
from config import get_default_image as _cfg_get_default_image
from config import set_default_image as _cfg_set_default_image
from domain.errors import (
    BusinessConflictError,
    ExternalDependencyError,
    InvalidArgumentError,
)
from infra.docker.client import DockerClient
from infra.docker.types import LocalImage
from infra.registry.client import RegistryClient

logger = logging.getLogger(__name__)

__all__ = [
    "ImageState",
    "ImageRow",
    "UploadResult",
    "DeleteResult",
    "list_images",
    "upload_image",
    "push_image",
    "set_default_image",
    "unset_default_image",
    "get_default_image",
    "delete_image",
]


class ImageState(str, Enum):
    """镜像操作状态（v4 §10.1 状态矩阵）。"""

    NOT_PUSHED = "not_pushed"   # 未推送：Push 可用、设为默认禁用、Delete 可用
    PUSHED = "pushed"           # 已推送且非默认：Push 禁用、设为默认可用、Delete 可用
    DEFAULT = "default"         # 当前默认镜像：Push 禁用、取消默认可用、Delete 禁用


@dataclass(frozen=True)
class ImageRow:
    """镜像列表行（按 RepoTag 一行，v4 §10.1）。"""

    image_id: str            # Image ID（短 12 位）
    registry: str            # Registry 主机[:端口]，无则为空
    name: str                # 仓库路径（registry 之后；可含命名空间）
    tag: str
    created: Optional[datetime]
    size: int
    state: ImageState
    # 操作可用性（供 UI/API 直接使用，判断逻辑收敛于此）
    can_push: bool
    can_set_default: bool
    can_cancel_default: bool
    can_delete: bool


@dataclass(frozen=True)
class UploadResult:
    """镜像上传结果（v4 §10.2）。"""

    loaded_tags: list[str]          # data 导入回传的 RepoTag
    target_refs: list[str]          # 按标准目标引用构建的引用（用于推送）
    pushed_refs: list[str]          # 已推送的引用（未勾选自动推送则为空）


@dataclass(frozen=True)
class DeleteResult:
    """镜像删除结果（v4 §10.6）：本地与 Registry 非事务。"""

    local_deleted: bool             # 本地是否已删除
    registry_deleted: bool          # Registry 是否已删除（勾选同步且成功时为 True）
    registry_failed: bool           # 勾选同步但 Registry 失败（保持本地结果并提示，不回滚）
    error_summary: Optional[str] = None


# ---------------------------------------------------------------------------
# 惰性单例（供测试注入）
# ---------------------------------------------------------------------------
_docker_client: Optional[DockerClient] = None
_registry_client: Optional[RegistryClient] = None


def _docker() -> DockerClient:
    global _docker_client
    if _docker_client is None:
        _docker_client = DockerClient()
    return _docker_client


def _registry() -> RegistryClient:
    global _registry_client
    if _registry_client is None:
        _registry_client = RegistryClient()
    return _registry_client


def _default_registry() -> str:
    return settings.image_default_registry


def _default_namespace() -> str:
    return settings.image_default_namespace


def _registry_host() -> str:
    """默认注册表主机（去除协议头/尾斜杠），用于构造 docker 风格引用（v4 §10.4）。"""
    return _strip_scheme(settings.image_default_registry)


def _strip_scheme(value: str) -> str:
    """去除协议头与尾斜杠（用于 docker 风格镜像引用）。"""
    value = value.strip()
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.rstrip("/")


# ---------------------------------------------------------------------------
# 引用解析
# ---------------------------------------------------------------------------
def _split_tag(full_ref: str) -> tuple[str, str]:
    """拆分 `registry/name:tag` 为 (repo_path, tag)；tag 位于最后一个 '/' 之后且非空，否则视为 latest。"""
    value = full_ref.strip()
    if not value:
        raise InvalidArgumentError("镜像引用不能为空")
    slash = value.rfind("/")
    colon = value.rfind(":")
    if colon > slash and colon + 1 < len(value):
        return value[:colon], value[colon + 1:]
    return value, "latest"


def _parse_ref(full_ref: str) -> tuple[str, str, str, str]:
    """解析完整引用为 (registry, namespace, name, tag)。

    - registry：首个以 `:` / `.` 或为 localhost 的段（无则为空）。
    - name：registry 之后的仓库路径；parent 段作为 namespace，最后一段为 name。
    - tag：最后一个冒号（位于最后 '/' 之后）分割。
    """
    repo_path, tag = _split_tag(full_ref)
    segments = repo_path.split("/")
    registry = ""
    if len(segments) > 1 and _looks_like_registry(segments[0]):
        registry = segments.pop(0)
    name_path = "/".join(segments)
    if "/" in name_path:
        namespace, name = name_path.rsplit("/", 1)
    else:
        namespace, name = "", name_path
    return registry, namespace, name, tag


def _looks_like_registry(segment: str) -> bool:
    return segment == "localhost" or "." in segment or ":" in segment


def _standard_ref(name: str, tag: str) -> str:
    """标准目标引用（v4 §10.4）：`<registry host>/<namespace>/<name>:<tag>`（docker 风格，无协议头）。"""
    return f"{_registry_host()}/{_default_namespace()}/{name}:{tag}"


# ---------------------------------------------------------------------------
# 列表与可用性
# ---------------------------------------------------------------------------
def list_images() -> list[ImageRow]:
    """列出本地 Docker 镜像并计算可用性（v4 §10.1 / §10.4）。

    - 一行 = 一个 RepoTag；无 RepoTag 的镜像（悬空镜像）跳过。
    - 已推送判断统一针对标准目标引用：`default_registry/default_namespace/{name}:{tag}`。
    - 当前默认镜像按 settings `default_image`（完整引用）匹配。
    """
    try:
        local_images = _docker().list_images()
    except Exception as exc:
        raise ExternalDependencyError("获取本地 Docker 镜像列表失败") from exc

    default_ref = _cfg_get_default_image()
    rows: list[ImageRow] = []
    for local in local_images:
        _append_local_rows(rows, local, default_ref)
    return rows


def _append_local_rows(rows: list[ImageRow], local: LocalImage, default_ref: Optional[str]) -> None:
    for repo_tag in local.tags:
        registry, namespace, name, tag = _parse_ref(repo_tag)
        standard = _standard_ref(name, tag)
        pushed = _is_pushed(name, tag)
        is_default = standard == default_ref
        state = ImageState.DEFAULT if is_default else (ImageState.PUSHED if pushed else ImageState.NOT_PUSHED)
        rows.append(
            ImageRow(
                image_id=local.id,
                registry=registry,
                name=name,
                tag=tag,
                created=local.created,
                size=local.size,
                state=state,
                can_push=not pushed,                        # 未推送才可 Push
                can_set_default=pushed and not is_default,  # 已推送且非默认 可设为默认
                can_cancel_default=is_default,              # 默认镜像可取消默认
                can_delete=not is_default,                  # 默认镜像禁止删除
            )
        )


def _is_pushed(name: str, tag: str) -> bool:
    try:
        return _registry().check_image_pushed(_default_registry(), _default_namespace(), name, tag)
    except Exception:
        # 不可达时不在列表层面失败：按未推送处理（保持列表可用），错误已由 infra 记录
        logger.exception("已推送判断失败（Registry 不可达），按未推送处理")
        return False


# ---------------------------------------------------------------------------
# 上传（load）
# ---------------------------------------------------------------------------
def upload_image(
    file_path: str,
    registry: str,
    namespace: str,
    auto_push: bool = False,
) -> UploadResult:
    """镜像上传（v4 §10.2）：校验扩展名 → docker load →（可选）推送 → 删除临时文件。

    - 文件类型仅允许 `UPLOAD_ALLOWED_EXTENSIONS`。
    - 临时文件 MUST 无论成败均删除。
    - 忽略镜像原 Registry/Namespace，以弹窗输入的 registry/namespace 构建目标引用。
    """
    suffix = Path(file_path).suffix.lower()
    try:
        if suffix not in Constants.UPLOAD_ALLOWED_EXTENSIONS.value:
            raise InvalidArgumentError(f"不支持的镜像文件类型: {suffix or '(无扩展名)'}")

        with open(file_path, "rb") as stream:
            loaded_tags = _docker().load_image(stream)

        host = _strip_scheme(registry) or _registry_host()
        target_refs = []
        for repo_tag in loaded_tags:
            _, _, name, tag = _parse_ref(repo_tag)
            target_refs.append(f"{host}/{namespace}/{name}:{tag}")

        pushed_refs: list[str] = []
        if auto_push:
            for target in target_refs:
                try:
                    _docker().push_image(target)
                    pushed_refs.append(target)
                except Exception as exc:
                    raise ExternalDependencyError(f"镜像推送失败: {target}") from exc
    except InvalidArgumentError:
        raise
    except Exception as exc:
        raise ExternalDependencyError("镜像导入到本地 Docker 失败") from exc
    finally:
        _remove_temp(file_path)

    return UploadResult(loaded_tags=loaded_tags, target_refs=target_refs, pushed_refs=pushed_refs)


def _remove_temp(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:  # noqa: BLE001 临时文件清理失败不阻断
        logger.warning("临时文件删除失败: %s (%s)", path, exc)


# ---------------------------------------------------------------------------
# 推送（Push）
# ---------------------------------------------------------------------------
def push_image(name: str, tag: str) -> str:
    """推送镜像至标准目标引用（v4 §10.3），不做 Registry 预验证；返回目标引用。"""
    target = _standard_ref(name, tag)
    try:
        _docker().push_image(target)
    except Exception as exc:
        raise ExternalDependencyError(f"镜像推送失败: {target}") from exc
    return target


# ---------------------------------------------------------------------------
# 默认镜像管理
# ---------------------------------------------------------------------------
def get_default_image() -> Optional[str]:
    """返回当前默认镜像完整引用；未配置返回 None（v4 §10.5）。"""
    return _cfg_get_default_image()


def set_default_image(full_ref: str) -> str:
    """设置默认镜像（完整引用）；仅已推送镜像可设为默认（v4 §10.5）。"""
    registry, namespace, name, tag = _parse_ref(full_ref)
    try:
        pushed = _registry().check_image_pushed(registry, namespace, name, tag)
    except Exception as exc:
        raise ExternalDependencyError("校验默认镜像已推送状态失败（Registry 不可达）") from exc
    if not pushed:
        raise BusinessConflictError("仅已推送的镜像可设为默认镜像")
    canonical = _standard_ref(name, tag)
    _cfg_set_default_image(canonical)
    return canonical


def unset_default_image() -> None:
    """取消默认镜像（v4 §10.5）。"""
    _cfg_set_default_image(None)


# ---------------------------------------------------------------------------
# 删除（本地 + 可选 Registry）
# ---------------------------------------------------------------------------
def delete_image(full_ref: str, also_registry: bool) -> DeleteResult:
    """镜像删除（v4 §10.6）：本地 rmi + 可选 Registry 同步删除，非事务。

    - 本地成功而 Registry 失败：保持本地删除结果并提示（`registry_failed=True`），不回滚。
    - Registry 删除失败不抛错（由调用方提示）；本地失败抛 `ExternalDependencyError`。
    """
    registry, namespace, name, tag = _parse_ref(full_ref)
    try:
        _docker().remove_image(full_ref)
    except Exception as exc:
        raise ExternalDependencyError(f"删除本地镜像失败: {full_ref}") from exc

    result = DeleteResult(local_deleted=True, registry_deleted=False, registry_failed=False)
    if not also_registry:
        return result
    if not registry:
        return DeleteResult(
            local_deleted=True,
            registry_deleted=False,
            registry_failed=True,
            error_summary="镜像未关联注册表，无法同步删除 Registry",
        )

    try:
        succeeded = _registry().delete_image(registry, namespace, name, tag)
        if succeeded:
            result = DeleteResult(local_deleted=True, registry_deleted=True, registry_failed=False)
        else:
            result = DeleteResult(
                local_deleted=True,
                registry_deleted=False,
                registry_failed=True,
                error_summary="Registry 删除未成功",
            )
    except Exception as exc:
        # 底层记录日志，这里保持本地结果并提示，不回滚
        logger.exception("Registry 删除失败（保持本地删除结果）: %s", full_ref)
        result = DeleteResult(
            local_deleted=True,
            registry_deleted=False,
            registry_failed=True,
            error_summary="Registry 删除失败",
        )
    return result
