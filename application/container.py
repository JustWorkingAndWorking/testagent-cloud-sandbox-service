"""
容器管理应用层（v4 §11、§14.5~§14.9）。

- 后端业务逻辑集中于此：创建（含创建限制原子校验）、操作（Start/Stop/Restart）、
  业务删除、恢复、立即删除、状态查询与剩余时间、日志查询、设置业务有效时长、业务条件查询。
- REST 接口仅承担必要输入/输出，不重复业务判断。
- 运行时状态来自 OpenSandbox（不落库）；业务数据写入 SQLite。
- 创建限制在进程内互斥锁 + 事务中执行（v4 §11.2、§6.3 语义；SQLite 单写者 + 进程互斥，单实例部署）。
"""

from __future__ import annotations

import logging
import math
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Iterator, NoReturn, Optional
from zoneinfo import ZoneInfo

from config import Constants, settings
from config import get_container_count_limit as _cfg_count_limit
from config import get_default_image as _cfg_default_image
from config import get_container_resource_limits as _cfg_resource_limits
from config import set_container_count_limit as _cfg_set_count_limit
from config import set_container_resource_limits as _cfg_set_resource_limits
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
from infra.opensandbox.client import OpenSandboxError, SandboxNotFoundError
from infra.opensandbox.types import (
    CreatedSandbox,
    SandboxEndpoint,
    SandboxMetrics,
    SandboxStatus,
)
from infra.orm import Container as ContainerRow
from infra.repositories import (
    AdminUserRepository,
    ContainerRepository,
    WhitelistUserRepository,
)

if TYPE_CHECKING:
    from infra.opensandbox.client import OpenSandboxClient

logger = logging.getLogger(__name__)

__all__ = [
    "CreateContainerParams",
    "CreatedContainer",
    "ContainerStatusView",
    "ExpirationView",
    "AdminContainerView",
    "ContainerLimitView",
    "AdminStateView",
    "get_status",
    "get_container_logs",
    "create_container",
    "get_opensandbox_client",
    "lifecycle_guard",
    "delete_missing_container_record",
    "start",
    "stop",
    "restart",
    "business_delete",
    "restore",
    "permanent_delete",
    "set_expiration",
    "query_container_ids",
    "list_admin_containers",
    "get_admin_container",
    "get_container_limit",
    "set_container_limit",
]

_TZ = ZoneInfo(Constants.TIMEZONE.value)

#: 创建限制临界区互斥（单实例部署，配合 SQLite 单写者保证原子性，v4 §13.1/§11.2）
_create_lock = threading.Lock()
#: 恢复与 Scheduler 物理清理共用的生命周期临界区（单实例部署）
_lifecycle_lock = threading.Lock()


@contextmanager
def lifecycle_guard() -> Iterator[None]:
    """串行化业务恢复与物理清理，避免两者对同一记录产生竞态。"""
    with _lifecycle_lock:
        yield


def delete_missing_container_record(container_id: str) -> None:
    """删除已确认不存在的远端容器对应的本地活跃记录。"""
    with lifecycle_guard():
        with session_scope() as session:
            repo = ContainerRepository(session)
            row = repo.get(container_id)
            if row is None or row.deleted_at is not None:
                return
            repo.delete(container_id)
    logger.info("远端容器不存在，已删除数据库记录: %s", container_id)


@dataclass(frozen=True)
class CreateContainerParams:
    user_id: str
    image: Optional[str] = None
    gitee_user: Optional[str] = None
    gitee_repository: Optional[str] = None
    gitee_branch: Optional[str] = None
    gitee_url: Optional[str] = ""
    authorize_general_account: Optional[bool] = None
    expiration_hours: Optional[int] = None
    #: CPU 核数，无单位，例如 0.5 / 1
    cpu: Optional[float] = None
    #: 内存大小，单位固定 Gi，例如 1 / 2
    memory: Optional[int] = None


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
    expires_at: Optional[str] = None
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    gitee_user: str = ""
    gitee_repository: str = ""
    gitee_url: str = ""


@dataclass(frozen=True)
class ExpirationView:
    container_id: str
    expires_at: Optional[str]


@dataclass(frozen=True)
class AdminContainerView:
    """管理端容器完整视图，合并持久化业务字段和运行时字段。"""

    container_id: str
    image: str
    user_id: str
    gitee_user: str
    gitee_repository: str
    gitee_branch: Optional[str]
    gitee_url: str
    created_at: str
    expiration_hours: int
    authorize_general_account: bool
    status: ContainerStatus
    endpoint: Optional[str]
    started_at: Optional[str]
    expires_at: Optional[str]
    cpu_usage: Optional[float]
    memory_usage: Optional[float]
    deleted_at: Optional[str]
    business_deleted: bool


@dataclass(frozen=True)
class ContainerLimitView:
    """管理端容器数量及资源限制视图。"""

    container_limit: int
    cpu: float
    memory: int


@dataclass(frozen=True)
class AdminStateView:
    """管理员首页基础统计视图。"""

    container_count: int
    whitelist_container_count: int
    admin_container_count: int
    whitelist_count: int
    admin_count: int


# ---------------------------------------------------------------------------
# 客户端惰性单例（供测试注入）
# ---------------------------------------------------------------------------
_opensandbox_client: Optional[OpenSandboxClient] = None


def get_opensandbox_client() -> OpenSandboxClient:
    """获取（惰性创建的）OpenSandbox 客户端；供业务与 Scheduler 复用，测试可注入替身。"""
    global _opensandbox_client
    client = _opensandbox_client
    if client is None:
        from infra.opensandbox.client import OpenSandboxClient

        client = OpenSandboxClient()
        _opensandbox_client = client
    return client


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
     环境变量注入 `TESTAGENT_CLOUD_USER_ID` / `TESTAGENT_CLOUD_GITEE_URL` /
       `TESTAGENT_CLOUD_GITEE_USER` / `TESTAGENT_CLOUD_GITEE_REPOSITORY` /
       `TESTAGENT_CLOUD_GITEE_BRANCH`（为空也注入空值）
       及 `TESTAGENT_CLOUD_AUTHORIZE_GENERAL_ACCOUNT`（true/false），并注入
       `PIP_INDEX_URL` / `NPM_CONFIG_REGISTRY` 代理源；CPU / 内存可选覆盖默认资源限制。
       OpenSandbox metadata 额外注入 `testagent-cloud=true` 用于来源识别。
     """
    _validate_required(params)
    gitee_url = _normalise_optional_gitee_value(params.gitee_url)
    gitee_user = _normalise_optional_gitee_value(params.gitee_user)
    gitee_repository = _normalise_optional_gitee_value(params.gitee_repository)
    image = _resolve_image(params)
    # 未指定时长时，使用创建后自动业务删除的默认有效时长。
    expiration_hours = params.expiration_hours if params.expiration_hours is not None \
        else settings.container_default_expiration_hours
    if expiration_hours < 0:
        raise InvalidArgumentError("expiration_hours 不能为负数")
    with _create_lock:
        with session_scope() as session:
            repo = ContainerRepository(session)
            _check_creation_limits(
                repo,
                params.user_id,
                gitee_user,
                gitee_repository,
            )

        env = {
            "TESTAGENT_CLOUD_MODE": "1",  # 标记容器为云端
            "TESTAGENT_CLOUD_USER_ID": params.user_id,
            "TESTAGENT_CLOUD_GITEE_USER": gitee_user,
            "TESTAGENT_CLOUD_GITEE_REPOSITORY": gitee_repository,
            "TESTAGENT_CLOUD_GITEE_BRANCH": params.gitee_branch or "",
            "TESTAGENT_CLOUD_GITEE_URL": gitee_url,
            "TESTAGENT_CLOUD_AUTHORIZE_GENERAL_ACCOUNT": (
                "true" if params.authorize_general_account is True else "false"
            ),
            "TESTAGENT_CLOUD_PIP_URL": settings.container_pip_index_url,
            "TESTAGENT_CLOUD_NPM_URL": settings.container_npm_registry,
        }
        container_name = uuid.uuid4().hex[:12]
        try:
            created: CreatedSandbox = get_opensandbox_client().create(
                image,
                name=container_name,
                env=env,
                metadata={"name": container_name, "testagent-cloud": "true"},
                resource_limits=_resource_limits(params),
            )
        except Exception as exc:
            if _is_image_not_found_error(exc):
                if params.image is None:
                    raise DefaultImageNotConfiguredError(
                        "默认镜像不存在，请联系管理员解决"
                    ) from exc
                raise InvalidArgumentError(
                    "镜像不存在，请检查镜像字段或者默认镜像设置后再创建容器"
                ) from exc
            _raise_backend_service_error("创建容器", exc)

        container_id = created.container_id
        created_at = _now_iso()
        try:
            with session_scope() as session:
                ContainerRepository(session).add(
                    ContainerRow(
                        container_id=container_id,
                        user_id=params.user_id,
                        gitee_url=gitee_url,
                        gitee_user=gitee_user,
                        gitee_repository=gitee_repository,
                        gitee_branch=params.gitee_branch,
                        image=image,
                        created_at=created_at,
                        expiration_hours=expiration_hours,
                        authorize_general_account=params.authorize_general_account is True,
                    )
                )
        except Exception as exc:
            logger.exception("容器已创建但保存数据库记录失败: %s", container_id)
            # noinspection broad-exception
            try:
                # 数据库写入失败时尽力回收远端容器，避免留下孤儿资源。
                get_opensandbox_client().delete(container_id)
            except Exception:  # noqa: BLE001
                logger.exception("数据库写入失败后回收远端容器失败: %s", container_id)
            raise ExternalDependencyError("保存容器记录失败") from exc

        return CreatedContainer(
            container_id=container_id,
            image=image,
            expiration_hours=expiration_hours,
            authorize_general_account=params.authorize_general_account is True,
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
    if not params.user_id or not params.user_id.strip():
        raise InvalidArgumentError("user_id 不能为空")
    gitee_values = (
        _normalise_optional_gitee_value(params.gitee_url),
        _normalise_optional_gitee_value(params.gitee_user),
        _normalise_optional_gitee_value(params.gitee_repository),
    )
    if any(gitee_values) and not all(gitee_values):
        raise InvalidArgumentError(
            "gitee_url、gitee_user、gitee_repository 必须同时填写或同时为空"
        )
    if params.cpu is not None and (
        isinstance(params.cpu, bool)
        or not isinstance(params.cpu, (int, float))
        or not math.isfinite(params.cpu)
        or params.cpu <= 0
    ):
        raise InvalidArgumentError("cpu 必须为正数")
    if params.memory is not None and (
        isinstance(params.memory, bool)
        or not isinstance(params.memory, int)
        or params.memory <= 0
    ):
        raise InvalidArgumentError("memory 必须为正整数")


def _normalise_optional_gitee_value(value: Optional[str]) -> str:
    """将未填写或仅含空白的 Gitee 字段统一为空字符串。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InvalidArgumentError("Gitee 字段必须为字符串")
    if not value.strip():
        return ""
    return value


def _resource_limits(params: CreateContainerParams) -> dict[str, str]:
    """解析最终资源值；调用方未覆盖时使用 limit 配置。"""
    default_cpu, default_memory = _cfg_resource_limits()
    cpu = params.cpu if params.cpu is not None else default_cpu
    memory = params.memory if params.memory is not None else default_memory
    limits: dict[str, str] = {"cpu": format(cpu, "g"), "memory": f"{memory}Gi"}
    return limits


def _check_creation_limits(
    repo: ContainerRepository,
    user_id: str,
    gitee_user: str,
    gitee_repository: str,
) -> None:
    """创建限制（v4 §11.2）：白名单跳过；模式限制 + 数量限制。

    计数口径：`running`/`pending` 计入、`stopped`/`business_deleted` 不计；
    当前以「非业务删除记录」计数（业务过期会先置 deleted_at 再停容器），
    手动停止的容器仍视作占用预留槽位。repository 模式按
    `user_id + (gitee_user, gitee_repository)` 区分仓库。
    """
    from application.whitelist import is_whitelisted

    if is_whitelisted(user_id):
        return

    mode = settings.container_create_limit_mode
    if mode == "user":
        if repo.count_active(user_id=user_id) >= 1:
            raise LimitReachedError("当前不允许同一用户创建多个容器")
    elif mode == "repository":
        if repo.count_active(
            user_id=user_id,
            gitee_repository=gitee_repository,
            gitee_user=gitee_user,
        ) >= 1:
            raise LimitReachedError("当前不允许同一用户为单个仓库创建多个容器")
    else:
        raise BusinessConflictError(f"不支持的创建限制模式: {mode}")

    limit = _cfg_count_limit()
    if 0 < limit <= repo.count_active():
        raise LimitReachedError("可用容器数量已达到上限")


# ---------------------------------------------------------------------------
# 状态查询与剩余时间（T6.7）
# ---------------------------------------------------------------------------
def get_status(container_id: str) -> ContainerStatusView:
    """实时查询 OpenSandbox 获取 status/endpoint/started_at 与预计过期时间。"""
    row = _require_active_record(container_id)
    try:
        status: SandboxStatus = get_opensandbox_client().get_status(container_id)
    except SandboxNotFoundError as exc:
        delete_missing_container_record(container_id)
        raise ContainerNotFoundError("后端容器不存在") from exc
    except Exception as exc:
        _raise_backend_service_error("获取容器状态", exc)

    business = map_runtime_state(status.state)
    endpoint: Optional[str] = None
    # noinspection broad-exception
    try:
        ep: SandboxEndpoint = get_opensandbox_client().get_endpoint(container_id, Constants.CONTAINER_SSH_PORT.value)
        endpoint = ep.endpoint
    except SandboxNotFoundError as exc:
        delete_missing_container_record(container_id)
        raise ContainerNotFoundError("后端容器不存在") from exc
    except Exception as exc:  # noqa: BLE001
        _raise_backend_service_error("获取容器端点", exc)

    cpu_usage, memory_usage = _get_metrics(container_id)

    return ContainerStatusView(
        container_id=container_id,
        status=business,
        endpoint=endpoint,
        started_at=status.transitioned_at,
        expires_at=add_hours_to_iso(row.created_at, row.expiration_hours),
        cpu_usage=cpu_usage,
        memory_usage=memory_usage,
        gitee_url=row.gitee_url,
        gitee_user=row.gitee_user,
        gitee_repository=row.gitee_repository,
    )


def get_container_logs(container_id: str) -> str:
    """读取指定容器日志；管理员可读取仍保留的业务删除记录。"""
    with session_scope() as session:
        if ContainerRepository(session).get(container_id) is None:
            raise ContainerNotFoundError("容器不存在")

    try:
        return get_opensandbox_client().get_logs(container_id)
    except SandboxNotFoundError as exc:
        raise ContainerNotFoundError("后端容器不存在") from exc
    except Exception as exc:
        _raise_backend_service_error("获取容器日志错误", exc)


# ---------------------------------------------------------------------------
# 容器操作（T6.3）
# ---------------------------------------------------------------------------
def start(container_id: str) -> None:
    """启动容器；已运行重复调用幂等成功（v4 §11.3）。"""
    _require_active_record(container_id)
    try:
        get_opensandbox_client().start(container_id)
    except Exception as exc:
        _raise_backend_service_error("启动容器", exc)


def stop(container_id: str) -> None:
    """正常停止；已停止重复调用幂等成功（v4 §11.3）。"""
    _require_active_record(container_id)
    try:
        get_opensandbox_client().stop(container_id)
    except Exception as exc:
        _raise_backend_service_error("停止容器", exc)


def restart(container_id: str) -> None:
    """停止并重新启动；Container ID 不变（v4 §11.3）。"""
    _require_active_record(container_id)
    try:
        get_opensandbox_client().restart(container_id)
    except Exception as exc:
        _raise_backend_service_error("重启容器", exc)


# ---------------------------------------------------------------------------
# 业务删除（T6.4）
# ---------------------------------------------------------------------------
def business_delete(container_id: str) -> None:
    """业务删除：OpenSandbox Stop + 写 `deleted_at`，底层容器保留。

    已业务删除的容器再次删除一律视为不存在（404 语义，v4 §14.9 由最新约定覆盖）。
    """
    with session_scope() as session:
        repo = ContainerRepository(session)
        row = repo.get(container_id)
        if row is None:
            raise ContainerNotFoundError("容器不存在")
        if row.deleted_at is not None:
            raise ContainerNotFoundError("容器不存在")
        try:
            get_opensandbox_client().stop(container_id)
        except Exception as exc:
            _raise_backend_service_error("停止容器", exc)
        repo.business_delete(container_id, _now_iso())


# ---------------------------------------------------------------------------
# 恢复（T6.5，仅管理 API）
# ---------------------------------------------------------------------------
def restore(container_id: str, expiration_hours: int) -> ContainerStatusView:
    """恢复：清除 `deleted_at`、重写 `created_at`（当前时间）与 `expiration_hours`，并启动容器。

    `authorize_general_account` 不重新指定，保持原值（变更 #2）。
    """
    if expiration_hours < 0:
        raise InvalidArgumentError("expiration_hours 不能为负数")
    with lifecycle_guard():
        with session_scope() as session:
            repo = ContainerRepository(session)
            row = repo.get(container_id)
            if row is None:
                raise ContainerNotFoundError("容器不存在")
            if row.deleted_at is None:
                raise BusinessConflictError("容器未处于业务删除状态，无法恢复")
            try:
                # OpenSandbox 的普通 start 对不存在容器按幂等成功处理；恢复必须先严格确认远端记录仍存在。
                get_opensandbox_client().get_status(container_id)
            except SandboxNotFoundError as exc:
                raise ContainerNotFoundError("后端容器不存在，无法恢复") from exc
            except Exception as exc:
                _raise_backend_service_error("检查容器状态", exc)
            try:
                get_opensandbox_client().start(container_id)
            except Exception as exc:
                _raise_backend_service_error("启动容器", exc)
            repo.business_restore(container_id, _now_iso(), expiration_hours)

    return get_status(container_id)


def _get_metrics(container_id: str) -> tuple[Optional[float], Optional[float]]:
    """读取容器资源使用率；旧测试替身未提供 metrics 时返回空值。"""
    get_metrics = getattr(get_opensandbox_client(), "get_metrics", None)
    if not callable(get_metrics):
        return None, None
    try:
        # noinspection calling-non-callable
        metrics = get_metrics(container_id)
    except SandboxNotFoundError as exc:
        delete_missing_container_record(container_id)
        raise ContainerNotFoundError("后端容器不存在") from exc
    except OpenSandboxError as exc:
        logger.error("获取容器资源使用率失败: %s: %s", container_id, exc)
        return None, None
    except Exception as exc:
        logger.error(
            "获取容器资源使用率失败: %s: %s: %s",
            container_id,
            type(exc).__name__,
            exc,
        )
        return None, None
    if not isinstance(metrics, SandboxMetrics):
        logger.error("获取容器资源使用率失败: %s: 返回类型无效", container_id)
        return None, None
    return metrics.cpu_usage, metrics.memory_usage


# ---------------------------------------------------------------------------
# 立即删除（T6.6，仅管理 API）
# ---------------------------------------------------------------------------
def permanent_delete(container_id: str) -> None:
    """立即删除：OpenSandbox Delete 物理删除底层容器 + 删除 SQLite 记录（v4 §11.4）。"""
    with session_scope() as session:
        repo = ContainerRepository(session)
        if repo.get(container_id) is None:
            raise ContainerNotFoundError("容器不存在")
    try:
        get_opensandbox_client().delete(container_id)
    except Exception as exc:
        _raise_backend_service_error("删除容器", exc)
    with session_scope() as session:
        ContainerRepository(session).delete(container_id)


# ---------------------------------------------------------------------------
# 设置业务有效时长（T6.8）
# ---------------------------------------------------------------------------
def set_expiration(container_id: str, expiration_hours: int) -> ExpirationView:
    """仅修改 `expiration_hours`，不重置 `created_at`；0 表示永不过期（v4 §14.8）。"""
    if expiration_hours < 0:
        raise InvalidArgumentError("expiration_hours 不能为负数")
    with lifecycle_guard():
        with session_scope() as session:
            repo = ContainerRepository(session)
            row = repo.get(container_id)
            if row is None or row.deleted_at is not None:
                raise ContainerNotFoundError("容器不存在")
            repo.update_expiration(container_id, expiration_hours)
            expiration = add_hours_to_iso(row.created_at, expiration_hours)
    return ExpirationView(container_id=container_id, expires_at=expiration)


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
# 管理端容器完整查询与数量限制
# ---------------------------------------------------------------------------
def list_admin_containers() -> list[AdminContainerView]:
    """列出全部容器，包括业务已删除记录。"""
    with session_scope() as session:
        rows = list(ContainerRepository(session).list_all())
    views: list[AdminContainerView] = []
    for row in rows:
        try:
            views.append(_to_admin_view(row))
        except ContainerNotFoundError:
            # 状态查询已同步清理远端缺失的本地活跃记录，不再返回该条目。
            continue
    return views


def get_admin_container(container_id: str) -> AdminContainerView:
    """查询管理端容器完整信息；业务已删除记录仍可查询。"""
    with session_scope() as session:
        row = ContainerRepository(session).get(container_id)
    if row is None:
        raise ContainerNotFoundError("容器不存在")
    return _to_admin_view(row)


def get_container_limit() -> ContainerLimitView:
    """读取容器数量及资源限制配置。"""
    cpu, memory = _cfg_resource_limits()
    return ContainerLimitView(
        container_limit=_cfg_count_limit(),
        cpu=cpu,
        memory=memory,
    )


def set_container_limit(
    container_limit: int,
    *,
    cpu: float,
    memory: int,
) -> ContainerLimitView:
    """设置容器数量及资源限制并返回最新限制视图。"""
    if (
        isinstance(container_limit, bool)
        or not isinstance(container_limit, int)
        or container_limit < 0
    ):
        raise InvalidArgumentError("container_limit 必须为非负整数")
    if (
        isinstance(cpu, bool)
        or not isinstance(cpu, (int, float))
        or not math.isfinite(cpu)
        or cpu <= 0
    ):
        raise InvalidArgumentError("cpu 必须为正数")
    if (
        isinstance(memory, bool)
        or not isinstance(memory, int)
        or memory <= 0
    ):
        raise InvalidArgumentError("memory 必须为正整数")
    _cfg_set_count_limit(container_limit)
    _cfg_set_resource_limits(cpu, memory)
    return get_container_limit()


def get_admin_state() -> AdminStateView:
    """读取未业务删除容器及白名单/管理员清单的基础统计。"""
    with session_scope() as session:
        container_repo = ContainerRepository(session)
        whitelist_ids = {
            row.user_id for row in WhitelistUserRepository(session).list_all()
        }
        admin_ids = {row.user_id for row in AdminUserRepository(session).list_all()}
        containers = container_repo.list_active()

    return AdminStateView(
        container_count=len(containers),
        whitelist_container_count=sum(row.user_id in whitelist_ids for row in containers),
        admin_container_count=sum(row.user_id in admin_ids for row in containers),
        whitelist_count=len(whitelist_ids),
        admin_count=len(admin_ids),
    )


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


def _raise_backend_service_error(operation: str, exc: Exception) -> NoReturn:
    """将 OpenSandbox 故障记录为一条简洁错误，并统一映射为 HTTP 502。"""
    if not isinstance(exc, OpenSandboxError):
        logger.error(
            "OpenSandbox %s失败: %s: %s",
            operation,
            type(exc).__name__,
            exc,
        )
    raise ExternalDependencyError("后端服务错误") from exc


def _is_image_not_found_error(exc: Exception) -> bool:
    """判断 OpenSandbox 创建失败是否由镜像不存在导致。"""
    markers = (
        "image not found",
        "image_not_found",
        "image-not-found",
        "镜像不存在",
        "镜像未找到",
        "manifest unknown",
        "manifest_unknown",
        "errimagepull",
        "imagepullbackoff",
        "no such image",
        "pull access denied",
        "failed to pull image",
        "repository does not exist",
    )
    pending: list[BaseException] = [exc]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))

        parts = [str(current)]
        error = getattr(current, "error", None)
        if error is not None:
            for attribute in ("code", "message"):
                value = getattr(error, attribute, None)
                if isinstance(value, str) and value:
                    parts.append(value)
        text = " ".join(parts).lower()
        if any(marker in text for marker in markers):
            return True
        if "image" in text and any(
            marker in text for marker in ("not found", "does not exist")
        ):
            return True

        cause = current.__cause__
        context = current.__context__
        if cause is not None:
            pending.append(cause)
        if context is not None:
            pending.append(context)
    return False


def _to_admin_view(row: ContainerRow) -> AdminContainerView:
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    if row.deleted_at is not None:
        status = ContainerStatus.BUSINESS_DELETED
        endpoint: Optional[str] = None
        started_at: Optional[str] = None
        expires_at = add_hours_to_iso(row.created_at, row.expiration_hours)
    else:
        runtime = _get_admin_runtime(row.container_id)
        status = runtime.status
        endpoint = runtime.endpoint
        started_at = runtime.started_at
        expires_at = runtime.expires_at
        cpu_usage = runtime.cpu_usage
        memory_usage = runtime.memory_usage

    return AdminContainerView(
        container_id=row.container_id,
        image=row.image,
        user_id=row.user_id,
        gitee_url=row.gitee_url,
        gitee_user=row.gitee_user,
        gitee_repository=row.gitee_repository,
        gitee_branch=row.gitee_branch,
        created_at=row.created_at,
        expiration_hours=row.expiration_hours,
        authorize_general_account=bool(row.authorize_general_account),
        status=status,
        endpoint=endpoint,
        started_at=started_at,
        expires_at=expires_at,
        cpu_usage=cpu_usage,
        memory_usage=memory_usage,
        deleted_at=row.deleted_at,
        business_deleted=row.deleted_at is not None,
    )


def _get_admin_runtime(container_id: str) -> ContainerStatusView:
    """优先使用 Scheduler 快照，首次刷新前才回退到实时查询。"""
    # 局部导入避免 application.container 与 scheduler.lifecycle 的模块循环依赖。
    from scheduler.lifecycle import get_cached_runtime, get_cached_status

    cached = get_cached_runtime(container_id)
    if cached is not None:
        return ContainerStatusView(
            container_id=container_id,
            status=cached.status,
            endpoint=cached.endpoint,
            started_at=cached.started_at,
            cpu_usage=cached.cpu_usage,
            memory_usage=cached.memory_usage,
        )

    # 保留旧缓存的兼容读取路径：测试或进程升级期间可能只有状态缓存。
    cached_status = get_cached_status(container_id)
    if cached_status is not None:
        return ContainerStatusView(container_id=container_id, status=cached_status)

    return get_status(container_id)
