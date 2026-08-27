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
    #: 业务保留时长（小时）
    container_retention_hours: int
    #: 业务删除时长默认值（小时，7 天）
    container_default_expiration_hours: int
    #: 容器数量限制默认值（settings 表未设置时使用）
    container_default_count_limit: int
    #: Registry 地址默认值
    image_default_registry: str
    #: 镜像命名空间默认值
    image_default_namespace: str
    #: REST 文档 Basic 鉴权用户名
    web_username: str
    #: REST 文档 Basic 鉴权密码
    web_password: str
    #: Scheduler 轮询周期（秒）
    scheduler_poll_interval_seconds: int
    #: REST API 监听端口（监听地址固定 0.0.0.0）
    rest_api_port: int
    #: 日志级别：DEBUG / INFO / WARNING / ERROR
    log_level: str


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
    container_retention_hours=_int("CONTAINER_RETENTION_HOURS", default=24, minimum=0),
    container_default_expiration_hours=_int(
        "CONTAINER_DEFAULT_EXPIRATION_HOURS", default=24 * 7, minimum=0
    ),
    container_default_count_limit=_int("CONTAINER_DEFAULT_COUNT_LIMIT", minimum=0),
    image_default_registry=_string("IMAGE_DEFAULT_REGISTRY"),
    image_default_namespace=_string("IMAGE_DEFAULT_NAMESPACE", "testagent"),
    web_username=_string("WEB_USERNAME"),
    web_password=_string("WEB_PASSWORD"),
    scheduler_poll_interval_seconds=_int("SCHEDULER_POLL_INTERVAL_SECONDS", 5, minimum=1),
    rest_api_port=_int("REST_API_PORT", 8080, minimum=1, maximum=65535),
    log_level=_log_level,
)


# ---------------------------------------------------------------------------
# settings 读写（v4 §4.3 / §6.2.2）：`default_image` / `container_count_limit`
# 应用层与接口层通过本模块访问，不直接操作 settings 表。
# ---------------------------------------------------------------------------

SETTINGS_KEY_DEFAULT_IMAGE = "default_image"
SETTINGS_KEY_CONTAINER_COUNT_LIMIT = "container_count_limit"


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
