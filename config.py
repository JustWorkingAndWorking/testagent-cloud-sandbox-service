"""
应用配置层：环境变量加载与校验、全局常量、settings 读写接口。

规范依据：
- v4 §5.1 环境变量（`TA_SS_*`）：必填缺失或非法值启动失败并明确报错，整数无法解析启动失败，
  `TA_SS_CONTAINER_CREATE_LIMIT_MODE` 仅允许 `user` / `repository`。
- v4 §5.2 全局常量（`Constants` 枚举）。
- v4 §4.3 / §6.2.2 settings（`default_image` / `container_count_limit`）读写归属本模块；
  应用层与接口层不直接操作 settings 表。

配置在模块加载时初始化（Python 模块缓存保证整个进程仅执行一次）。
"""

from __future__ import annotations

import math
import os
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterator, Literal, Optional, cast

__all__ = [
    "ConfigError",
    "Constants",
    "settings",
    "get_default_image",
    "set_default_image",
    "get_container_count_limit",
    "set_container_count_limit",
    "get_container_resource_limits",
    "set_container_resource_limits",
]

_ENV_PREFIX = "TA_SS_"

_LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")

# 容器创建限制
CONTAINER_CREATE_LIMIT_MODES: tuple[str, ...] = ("user", "repository")
#: 容器创建限制模式可取值
ContainerCreateLimitMode = Literal["user", "repository"]


class ConfigError(Exception):
    """配置缺失或非法，导致服务启动失败。"""
    ...


class Constants(Enum):
    """全局常量（v4 §5.2），不通过环境变量或数据库配置。"""

    #: 项目根目录
    APP_ROOT_PATH = str(Path(__file__).resolve().parent)
    #: SQLite 文件路径（v4 §6.3）
    DB_PATH = "./data/sandbox.db"
    #: 镜像上传临时文件目录
    UPLOAD_TEMP_PATH = "./temp"
    #: 允许上传的镜像文件扩展名
    UPLOAD_ALLOWED_EXTENSIONS = (".tar", ".tar.gz")
    #: 容器内 sshd 监听端口
    CONTAINER_SSH_PORT = 22
    #: 管理端分页默认每页条数（SHOULD）
    DEFAULT_PAGE_SIZE = 20
    #: 系统统一时区（UTC+8）
    TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True)
class Settings:
    """加载并校验后的运行配置（v4 §5.1）。"""

    #: OpenSandbox 服务地址（必填）
    opensandbox_url: str
    #: OpenSandbox 认证 Key；未设置则不发送
    opensandbox_api_key: Optional[str]
    #: 容器创建限制模式：user / repository
    container_create_limit_mode: ContainerCreateLimitMode
    #: 容器创建后的默认有效时长（小时）；到期后由 Scheduler 自动业务删除
    container_default_expiration_hours: int
    #: 业务删除后的保留时长（小时）；到期后由 Scheduler 自动物理删除
    container_retention_hours: int
    #: 容器数量限制默认值（settings 表未设置时使用）；0 表示取消数量限制
    container_default_count_limit: int
    #: Registry 地址默认值
    image_default_registry: str
    #: 镜像命名空间默认值
    image_default_namespace: str
    #: Scheduler 轮询周期（秒）
    scheduler_poll_interval_seconds: int
    #: REST API 监听端口（监听地址固定 0.0.0.0）
    rest_api_port: int
    #: REST 文档 Basic 鉴权用户名
    rest_api_username: str
    #: REST 文档 Basic 鉴权密码
    rest_api_password: str
    #: 日志级别：DEBUG / INFO / WARNING / ERROR
    log_level: str
    #: 容器内 PIP 包索引地址；为空时仍向容器注入空值
    container_pip_index_url: str = ""
    #: 容器内 NPM Registry 地址；为空时仍向容器注入空值
    container_npm_registry: str = ""
    #: 容器默认 CPU 核数；可由管理端 limit 配置覆盖
    container_default_cpu: float = 1.0
    #: 容器默认内存大小，单位 Gi；可由管理端 limit 配置覆盖
    container_default_memory: int = 1


def _string(name: str, default: Optional[str] = None) -> str:
    value = os.environ.get(_ENV_PREFIX + name)
    if not value:
        if default is None:
            raise ConfigError(f"缺少必填环境变量 {_ENV_PREFIX + name}")
        return default
    return value


def _string_or_none(name: str) -> Optional[str]:
    value = os.environ.get(_ENV_PREFIX + name)
    return value if value else None


def _image_registry() -> str:
    value = _string("IMAGE_DEFAULT_REGISTRY")
    # noinspection HttpUrlsUsage
    for prefix in ("http://", "https://"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def _int(
    name: str,
    default: Optional[int] = None,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    raw = os.environ.get(_ENV_PREFIX + name)
    if raw is None or raw == "":
        if default is None:
            raise ConfigError(f"缺少必填环境变量 {_ENV_PREFIX + name}")
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            raise ConfigError(f"环境变量 {_ENV_PREFIX + name} 无法解析为整数: {raw!r}")

    if minimum is not None and value < minimum:
        raise ConfigError(
            f"环境变量 {_ENV_PREFIX + name} 必须大于等于 {minimum}: {value}"
        )
    if maximum is not None and value > maximum:
        raise ConfigError(
            f"环境变量 {_ENV_PREFIX + name} 必须小于等于 {maximum}: {value}"
        )
    return value


def _float(
    name: str,
    default: Optional[float] = None,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    raw = os.environ.get(_ENV_PREFIX + name)
    if raw is None or raw == "":
        if default is None:
            raise ConfigError(f"缺少必填环境变量 {_ENV_PREFIX + name}")
        value = default
    else:
        try:
            value = float(raw)
        except ValueError:
            raise ConfigError(f"环境变量 {_ENV_PREFIX + name} 无法解析为数字: {raw!r}")

    if not math.isfinite(value):
        raw: str
        raise ConfigError(f"环境变量 {_ENV_PREFIX + name} 必须为有限数字: {raw!r}")
    if minimum is not None and value < minimum:
        raise ConfigError(
            f"环境变量 {_ENV_PREFIX + name} 必须大于等于 {minimum}: {value}"
        )
    if maximum is not None and value > maximum:
        raise ConfigError(
            f"环境变量 {_ENV_PREFIX + name} 必须小于等于 {maximum}: {value}"
        )
    return value


_create_limit_mode = cast(ContainerCreateLimitMode, _string("CONTAINER_CREATE_LIMIT_MODE"))
if _create_limit_mode not in CONTAINER_CREATE_LIMIT_MODES:
    raise ConfigError(
        f"环境变量 {_ENV_PREFIX}CONTAINER_CREATE_LIMIT_MODE 仅允许 "
        f"{' / '.join(CONTAINER_CREATE_LIMIT_MODES)}，当前值: {_create_limit_mode!r}"
    )

_log_level = _string("LOG_LEVEL", "INFO")
if _log_level not in _LOG_LEVELS:
    raise ConfigError(
        f"环境变量 {_ENV_PREFIX}LOG_LEVEL 仅允许 {' / '.join(_LOG_LEVELS)}，当前值: {_log_level!r}"
    )

settings: Settings = Settings(
    opensandbox_url=_string("OPENSANDBOX_URL"),
    opensandbox_api_key=_string_or_none("OPENSANDBOX_API_KEY"),
    container_create_limit_mode=_create_limit_mode,
    container_retention_hours=_int("CONTAINER_RETENTION_HOURS", default=24 * 7, minimum=0),
    container_default_expiration_hours=_int(
        "CONTAINER_DEFAULT_EXPIRATION_HOURS", default=24, minimum=0
    ),
    container_default_count_limit=_int("CONTAINER_DEFAULT_COUNT_LIMIT", default=0, minimum=0),
    image_default_registry=_image_registry(),
    image_default_namespace=_string("IMAGE_DEFAULT_NAMESPACE", "testagent"),
    rest_api_username=_string("REST_API_USERNAME"),
    rest_api_password=_string("REST_API_PASSWORD"),
    scheduler_poll_interval_seconds=_int("SCHEDULER_POLL_INTERVAL_SECONDS", 5, minimum=1),
    rest_api_port=_int("REST_API_PORT", 8080, minimum=1, maximum=65535),
    log_level=_log_level,
    container_pip_index_url=_string("PROXY_PIP_INDEX_URL", ""),
    container_npm_registry=_string("PROXY_NPM_REGISTRY", ""),
    container_default_cpu=_float("CONTAINER_DEFAULT_CPU", 1.0, minimum=0.01),
    container_default_memory=_int("CONTAINER_DEFAULT_MEMORY", 1, minimum=1),
)


# ---------------------------------------------------------------------------
# settings 读写（v4 §4.3 / §6.2.2）：默认镜像、容器数量及资源限制
# 应用层与接口层通过本模块访问，不直接操作 settings 表。
# ---------------------------------------------------------------------------

SETTINGS_KEY_DEFAULT_IMAGE = "default_image"
SETTINGS_KEY_CONTAINER_COUNT_LIMIT = "container_count_limit"
SETTINGS_KEY_CONTAINER_CPU_LIMIT = "container_cpu_limit"
SETTINGS_KEY_CONTAINER_MEMORY_LIMIT = "container_memory_limit"


@contextmanager
def _settings_scope() -> Iterator:
    from infra.db import session_scope
    from infra.repositories import SettingsRepository

    with session_scope() as session:
        yield SettingsRepository(session)


def get_default_image() -> Optional[str]:
    """读取默认镜像完整引用；未设置返回 None。"""
    with _settings_scope() as repo:
        row = repo.get(SETTINGS_KEY_DEFAULT_IMAGE)
    return row.value if row is not None else None


def set_default_image(value: Optional[str]) -> None:
    """设置默认镜像完整引用；传 None 表示取消默认。"""
    with _settings_scope() as repo:
        if value is None:
            repo.delete(SETTINGS_KEY_DEFAULT_IMAGE)
        else:
            repo.set(SETTINGS_KEY_DEFAULT_IMAGE, value)


def get_container_count_limit() -> int:
    """读取容器数量限制；数据库未设置时返回 `TA_SS_CONTAINER_DEFAULT_COUNT_LIMIT`。"""
    with _settings_scope() as repo:
        row = repo.get(SETTINGS_KEY_CONTAINER_COUNT_LIMIT)
    if row is None:
        return settings.container_default_count_limit
    try:
        return int(row.value)
    except ValueError:
        raise ConfigError(f"数据库中的容器数量限制非法: {row.value!r}")


def set_container_count_limit(value: int) -> None:
    """设置容器数量限制。"""
    with _settings_scope() as repo:
        repo.set(SETTINGS_KEY_CONTAINER_COUNT_LIMIT, str(int(value)))


def get_container_resource_limits() -> tuple[float, int]:
    """读取容器 CPU/内存限制；数据库未设置时返回环境变量默认值。"""
    with _settings_scope() as repo:
        cpu_row = repo.get(SETTINGS_KEY_CONTAINER_CPU_LIMIT)
        memory_row = repo.get(SETTINGS_KEY_CONTAINER_MEMORY_LIMIT)

    if cpu_row is None:
        cpu = settings.container_default_cpu
    else:
        try:
            cpu = float(cpu_row.value)
        except ValueError:
            raise ConfigError(f"数据库中的容器 CPU 限制非法: {cpu_row.value!r}")
        if not math.isfinite(cpu) or cpu <= 0:
            raise ConfigError(f"数据库中的容器 CPU 限制非法: {cpu_row.value!r}")

    if memory_row is None:
        memory = settings.container_default_memory
    else:
        try:
            memory = int(memory_row.value)
        except ValueError:
            raise ConfigError(f"数据库中的容器内存限制非法: {memory_row.value!r}")
        if memory <= 0:
            raise ConfigError(f"数据库中的容器内存限制非法: {memory_row.value!r}")

    return cpu, memory


def set_container_resource_limits(cpu: float, memory: int) -> None:
    """设置容器 CPU/内存限制。"""
    if (
        isinstance(cpu, bool)
        or not isinstance(cpu, (int, float))
        or not math.isfinite(cpu)
        or cpu <= 0
    ):
        raise ConfigError("容器 CPU 限制必须为正数")
    if isinstance(memory, bool) or not isinstance(memory, int) or memory <= 0:
        raise ConfigError("容器内存限制必须为正整数")
    with _settings_scope() as repo:
        repo.set(SETTINGS_KEY_CONTAINER_CPU_LIMIT, format(cpu, "g"))
        repo.set(SETTINGS_KEY_CONTAINER_MEMORY_LIMIT, str(memory))
