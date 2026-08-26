"""
容器管理应用层（v4 §11、§14.5~§14.9）。

- 后端业务逻辑集中于此：创建（含创建限制原子校验）、操作（Start/Stop/Kill/Restart）、
  业务删除、恢复、立即删除、状态查询与剩余时间、设置业务有效时长、业务条件查询。
- REST 与 Web 仅承担必要输入/输出，不重复业务判断。
- 运行时状态来自 OpenSandbox（不落库）；业务数据写入 SQLite。
- 创建限制在进程内互斥锁 + 事务中执行（v4 §11.2、§6.3 语义；SQLite 单写者 + 进程互斥，单实例部署）。
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

from config import Constants, settings
from config import get_container_count_limit as _cfg_count_limit
from config import get_default_image as _cfg_default_image
from domain.errors import (
    BusinessConflictError,
    ContainerNotFoundError,
    DefaultImageNotConfiguredError,
    ExternalDependencyError,
    InvalidArgumentError,
    LimitReachedError,
)
from domain.models import ContainerStatus, add_hours_to_iso, map_runtime_state
from infra.db import session_scope
from infra.opensandbox.client import SandboxNotFoundError
from infra.opensandbox.types import CreatedSandbox, SandboxEndpoint, SandboxStatus
from infra.orm import Container as ContainerRow
from infra.repositories import ContainerRepository

if TYPE_CHECKING:
    from infra.opensandbox.client import OpenSandboxClient

logger = logging.getLogger(__name__)

__all__ = [
    "CreateContainerParams",
    "CreatedContainer",
    "ContainerStatusView",
    "ExpirationView",
    "get_status",
    "create_container",
    "start",
    "stop",
    "kill",
    "restart",
    "business_delete",
    "restore",
    "permanent_delete",
    "set_expiration",
    "query_container_ids",
]

_TZ = ZoneInfo(Constants.TIMEZONE.value)

#: 创建限制临界区互斥（单实例部署，配合 SQLite 单写者保证原子性，v4 §13.1/§11.2）
_create_lock = threading.Lock()


@dataclass(frozen=True)
class CreateContainerParams:
    user_id: str
    gitee_user: str
    gitee_repository: str
    gitee_branch: Optional[str] = None
    expiration_hours: Optional[int] = None
    image: Optional[str] = None
    authorize_general_account: bool = True


@dataclass(frozen=True)
class CreatedContainer:
    container_id: str
    image: str
    expiration_hours: int
    authorize_general_account: bool
    created_at: str
    status: ContainerStatus


@dataclass(frozen=True)
class ContainerStatusView:
    container_id: str
    status: ContainerStatus
    endpoint: Optional[str] = None
    started_at: Optional[str] = None
    remaining_time: Optional[int] = None


@dataclass(frozen=True)
class ExpirationView:
    container_id: str
    expiration_hours: int
    expiration: Optional[str]


# ---------------------------------------------------------------------------
# 客户端惰性单例（供测试注入）
# ---------------------------------------------------------------------------
_opensandbox_client: Optional[OpenSandboxClient] = None


def _opensandbox() -> OpenSandboxClient:
    global _opensandbox_client
    if _opensandbox_client is None:
        from infra.opensandbox.client import OpenSandboxClient

        _opensandbox_client = OpenSandboxClient()
    return _opensandbox_client


def _now() -> datetime:
    return datetime.now(_TZ)


def _now_iso() -> str:
    return _now().isoformat()


# ---------------------------------------------------------------------------
# 容器创建（T6.1 + T6.2）
# ---------------------------------------------------------------------------
def create_container(params: CreateContainerParams) -> CreatedContainer:
    """创建并自动启动容器（v4 §11.1）。

    - 镜像：`params.image` 为空则使用默认镜像；默认镜像未配置抛 400 语义错误。
    - 创建限制（模式 / 数量）在此校验，白名单用户跳过全部；并发通过进程互斥 + SQLite 单写者保证。
    - 容器名：随机字符串（仅表示容器本身，不承载业务信息）；端口固定 22；
      环境变量注入 `AGENT_USER_ID` / `AGENT_GITEE_USER` / `AGENT_GITEE_REPOSITORY` /
      `AGENT_GITEE_BRANCH`（为空也注入空值）及 `AGENT_AUTHORIZE_GENERAL_ACCOUNT`（true/false）。
    """
    image = _resolve_image(params)
    expiration_hours = params.expiration_hours if params.expiration_hours is not None \
        else settings.container_default_expiration_hours
    if expiration_hours < 0:
        raise InvalidArgumentError("expiration_hours 不能为负数")
    _validate_required(params)

    with _create_lock:
        with session_scope() as session:
            repo = ContainerRepository(session)
            _check_creation_limits(repo, params.user_id, params.gitee_repository)

        env = {
            "AGENT_USER_ID": params.user_id,
            "AGENT_GITEE_USER": params.gitee_user,
            "AGENT_GITEE_REPOSITORY": params.gitee_repository,
            "AGENT_GITEE_BRANCH": params.gitee_branch or "",
            "AGENT_AUTHORIZE_GENERAL_ACCOUNT": "true" if params.authorize_general_account else "false",
        }
        container_name = uuid.uuid4().hex[:12]
        try:
            created: CreatedSandbox = _opensandbox().create(
                image,
                name=container_name,
                env=env,
                metadata={"name": container_name},
            )
        except Exception as exc:
            raise ExternalDependencyError("创建容器失败") from exc

        container_id = created.container_id
        created_at = _now_iso()
        with session_scope() as session:
            ContainerRepository(session).add(
                ContainerRow(
                    container_id=container_id,
                    user_id=params.user_id,
                    gitee_user=params.gitee_user,
                    gitee_repository=params.gitee_repository,
                    gitee_branch=params.gitee_branch,
                    image=image,
                    created_at=created_at,
                    expiration_hours=expiration_hours,
                    authorize_general_account=params.authorize_general_account,
                )
            )

        return CreatedContainer(
            container_id=container_id,
            image=image,
            expiration_hours=expiration_hours,
            authorize_general_account=bool(params.authorize_general_account),
            created_at=created_at,
            status=ContainerStatus.PENDING,
        )


def _resolve_image(params: CreateContainerParams) -> str:
    if params.image:
        return params.image
    image = _cfg_default_image()
    if not image:
        raise DefaultImageNotConfiguredError("没有提供默认镜像，请联系管理员解决")
    return image


def _validate_required(params: CreateContainerParams) -> None:
    for field, value in (
        ("user_id", params.user_id),
        ("gitee_user", params.gitee_user),
        ("gitee_repository", params.gitee_repository),
    ):
        if not value or not str(value).strip():
            raise InvalidArgumentError(f"{field} 不能为空")


def _check_creation_limits(repo: ContainerRepository, user_id: str, gitee_repository: str) -> None:
    """创建限制（v4 §11.2）：白名单跳过；模式限制 + 数量限制。

    计数口径：`running`/`pending` 计入、`stopped`/`business_deleted` 不计；
    当前以「非业务删除记录」计数（业务过期会先置 deleted_at 再停容器），
    手动停止的特例仅在 Web 明确 Stop 后存在，仍视作占用预留槽位。
    """
    from application.whitelist import is_whitelisted

    if is_whitelisted(user_id):
        return

    mode = settings.container_create_limit_mode
    if mode == "user":
        if repo.count_active(user_id=user_id) >= 1:
            raise LimitReachedError("当前不允许同一用户创建多个容器")
    elif mode == "repository":
        if repo.count_active(user_id=user_id, gitee_repository=gitee_repository) >= 1:
            raise LimitReachedError("当前不允许同一用户为单个仓库创建多个容器")
    else:
        raise BusinessConflictError(f"不支持的创建限制模式: {mode}")

    limit = _cfg_count_limit()
    if repo.count_active() >= limit:
        raise LimitReachedError("可用容器数量已达到上限")


# ---------------------------------------------------------------------------
# 状态查询与剩余时间（T6.7）
# ---------------------------------------------------------------------------
def get_status(container_id: str) -> ContainerStatusView:
    """实时查询 OpenSandbox 获取 status/endpoint/started_at；remaining_time 由业务数据计算。"""
    row = _require_active_record(container_id)
    try:
        status: SandboxStatus = _opensandbox().get_status(container_id)
    except SandboxNotFoundError:
        status = SandboxStatus(state="TERMINATED")
    except Exception as exc:
        raise ExternalDependencyError("获取容器状态失败") from exc

    business = map_runtime_state(status.state)
    endpoint: Optional[str] = None
    try:
        ep: SandboxEndpoint = _opensandbox().get_endpoint(container_id, Constants.CONTAINER_SSH_PORT.value)
        endpoint = ep.endpoint
    except Exception:  # noqa: BLE001 端点获取失败不影响状态返回
        logger.exception("获取容器端点失败: %s", container_id)

    return ContainerStatusView(
        container_id=container_id,
        status=business,
        endpoint=endpoint,
        started_at=status.transitioned_at,
        remaining_time=_remaining_seconds(row.created_at, row.expiration_hours),
    )


def _remaining_seconds(created_at: str, expiration_hours: int) -> Optional[int]:
    """剩余秒数；`expiration_hours == 0`（永不过期）返回 None；已过期返回 0。"""
    if expiration_hours <= 0:
        return None
    expires = add_hours_to_iso(created_at, expiration_hours)
    if expires is None:
        return None
    seconds = int((datetime.fromisoformat(expires) - _now()).total_seconds())
    return max(seconds, 0)


# ---------------------------------------------------------------------------
# 容器操作（T6.3）
# ---------------------------------------------------------------------------
def start(container_id: str) -> None:
    """启动容器；已运行重复调用幂等成功（v4 §11.3）。"""
    _require_active_record(container_id)
    try:
        _opensandbox().start(container_id)
    except Exception as exc:
        raise ExternalDependencyError("启动容器失败") from exc


def stop(container_id: str) -> None:
    """正常停止；已停止重复调用幂等成功（v4 §11.3）。"""
    _require_active_record(container_id)
    try:
        _opensandbox().stop(container_id)
    except Exception as exc:
        raise ExternalDependencyError("停止容器失败") from exc


def kill(container_id: str) -> None:
    """强制终止，最终状态 stopped；已停止重复调用幂等成功（v4 §11.3）。"""
    _require_active_record(container_id)
    try:
        _opensandbox().kill(container_id)
    except Exception as exc:
        raise ExternalDependencyError("强制终止容器失败") from exc


def restart(container_id: str) -> None:
    """停止并重新启动；Container ID 不变（v4 §11.3）。"""
    _require_active_record(container_id)
    try:
        _opensandbox().restart(container_id)
    except Exception as exc:
        raise ExternalDependencyError("重启容器失败") from exc


# ---------------------------------------------------------------------------
# 业务删除（T6.4）
# ---------------------------------------------------------------------------
def business_delete(container_id: str) -> None:
    """业务删除：OpenSandbox Stop + 写 `deleted_at`，底层容器保留；重复调用幂等成功（v4 §11.4/14.9）。"""
    with session_scope() as session:
        repo = ContainerRepository(session)
        row = repo.get(container_id)
        if row is None:
            raise ContainerNotFoundError("容器不存在")
        if row.deleted_at is not None:
            return  # 已业务删除，幂等成功
        try:
            _opensandbox().stop(container_id)
        except Exception as exc:
            raise ExternalDependencyError("停止容器失败") from exc
        repo.business_delete(container_id, _now_iso())


# ---------------------------------------------------------------------------
# 恢复（T6.5，仅 Web）
# ---------------------------------------------------------------------------
def restore(container_id: str, expiration_hours: int) -> ContainerStatusView:
    """恢复：清除 `deleted_at`、重写 `created_at`（当前时间）与 `expiration_hours`，并启动容器。

    `authorize_general_account` 不重新指定，保持原值（变更 #2）。
    """
    if expiration_hours < 0:
        raise InvalidArgumentError("expiration_hours 不能为负数")
    with session_scope() as session:
        repo = ContainerRepository(session)
        row = repo.get(container_id)
        if row is None:
            raise ContainerNotFoundError("容器不存在")
        if row.deleted_at is None:
            raise BusinessConflictError("容器未处于业务已删除状态，无法恢复")
        try:
            _opensandbox().start(container_id)
        except Exception as exc:
            raise ExternalDependencyError("启动容器失败") from exc
        repo.business_restore(container_id, _now_iso(), expiration_hours)

    return get_status(container_id)


# ---------------------------------------------------------------------------
# 立即删除（T6.6，仅 Web）
# ---------------------------------------------------------------------------
def permanent_delete(container_id: str) -> None:
    """立即删除：OpenSandbox Delete 物理删除底层容器 + 删除 SQLite 记录（v4 §11.4）。"""
    with session_scope() as session:
        repo = ContainerRepository(session)
        if repo.get(container_id) is None:
            raise ContainerNotFoundError("容器不存在")
    try:
        _opensandbox().delete(container_id)
    except Exception as exc:
        raise ExternalDependencyError("删除容器失败") from exc
    with session_scope() as session:
        ContainerRepository(session).delete(container_id)


# ---------------------------------------------------------------------------
# 设置业务有效时长（T6.8）
# ---------------------------------------------------------------------------
def set_expiration(container_id: str, expiration_hours: int) -> ExpirationView:
    """仅修改 `expiration_hours`，不重置 `created_at`；0 表示永不过期（v4 §14.8）。"""
    if expiration_hours < 0:
        raise InvalidArgumentError("expiration_hours 不能为负数")
    with session_scope() as session:
        repo = ContainerRepository(session)
        row = repo.get(container_id)
        if row is None or row.deleted_at is not None:
            raise ContainerNotFoundError("容器不存在")
        repo.update_expiration(container_id, expiration_hours)
        expiration = add_hours_to_iso(row.created_at, expiration_hours)
    return ExpirationView(container_id=container_id, expiration_hours=expiration_hours, expiration=expiration)


# ---------------------------------------------------------------------------
# 业务条件查询（REST GET /containers）
# ---------------------------------------------------------------------------
def query_container_ids(
    user_id: str,
    *,
    gitee_user: Optional[str] = None,
    gitee_repository: Optional[str] = None,
    gitee_branch: Optional[str] = None,
) -> list[str]:
    """按业务条件查询容器 ID（AND 组合，不含业务已删除，v4 §14.6）。

    `user_id` 必填：REST 端点无认证，禁止不带用户标识枚举全部容器。
    """
    if not user_id or not user_id.strip():
        raise InvalidArgumentError("user_id 不能为空")
    with session_scope() as session:
        rows = ContainerRepository(session).list_active(
            user_id=user_id,
            gitee_user=gitee_user,
            gitee_repository=gitee_repository,
            gitee_branch=gitee_branch,
        )
        return [r.container_id for r in rows]


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _require_active_record(container_id: str) -> ContainerRow:
    """取业务有效（非业务已删除）容器记录；不存在或已业务删除抛 404 语义错误。"""
    with session_scope() as session:
        row = ContainerRepository(session).get(container_id)
    if row is None or row.deleted_at is not None:
        raise ContainerNotFoundError("容器不存在")
    return row
