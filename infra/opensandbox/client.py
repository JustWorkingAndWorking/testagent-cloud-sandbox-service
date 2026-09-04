"""
OpenSandbox 集成层（v4 §8）。

- 使用 OpenSandbox 官方 Python 包（同步封装 `SandboxSync`）作为客户端，封装于本模块；
  业务层与接口层不直接依赖原始包细节（v4 §8.1）。
- 容器日志通过 OpenSandbox 管理面 diagnostics plain-text API 读取，固定请求最后 10000 行。
- 连接地址与可选 API Key 来自环境变量（v4 §5.1 `TA_SS_OPENSANDBOX_*`）；未设置 Key 不发送（v4 §8.4）。
- 调用失败或不可达时 MUST 将底层详细错误写日志，对外只抛合理摘要（v4 §8.4）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Optional, TypeVar
from urllib.parse import quote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from httpx import Client as HttpxClient
from opensandbox.config.connection_sync import ConnectionConfigSync
from opensandbox.sync.sandbox import SandboxSync

from config import Constants, settings
from infra.opensandbox.types import (
    CreatedSandbox,
    SandboxEndpoint,
    SandboxMetrics,
    SandboxStatus,
)

logger = logging.getLogger(__name__)

__all__ = [
    "OpenSandboxClient",
    "OpenSandboxError",
    "SandboxNotFoundError",
]

#: 默认资源限额（服务端创建沙箱必需 `resourceLimits` 字段）
_DEFAULT_RESOURCE_LIMITS: dict[str, str] = {"cpu": "1", "memory": "1Gi"}
_LOG_TAIL = 10000
_TIMEZONE = ZoneInfo(Constants.TIMEZONE.value)
_STOPPED_STATES = frozenset({"PAUSED", "EXITED", "STOPPED", "TERMINATED", "DEAD"})

_T = TypeVar("_T")


class OpenSandboxError(Exception):
    """OpenSandbox 调用失败时抛出的对外摘要错误（底层细节已写日志）。"""


class SandboxNotFoundError(OpenSandboxError):
    """容器不存在（未找到 / 已被终止删除）；调用方可据此按「已删除 / 最终态」处理。"""


class OpenSandboxClient:
    """OpenSandbox 官方 Python 包客户端封装。"""

    def __init__(self, timeout: float = 30.0) -> None:
        self._config = ConnectionConfigSync(
            domain=settings.opensandbox_url,
            api_key=settings.opensandbox_api_key,
            request_timeout=timedelta(seconds=timeout),
            disable_metrics=True,
        )

    def create(
        self,
        image: str,
        *,
        name: str,
        env: dict[str, str],
        metadata: Optional[dict[str, str]] = None,
        skip_health_check: bool = True,
        resource_limits: Optional[dict[str, str]] = None,
    ) -> CreatedSandbox:
        """创建并自动启动容器（v4 §11.1）；`timeout=None` 表示生命周期由本服务管理。

        服务端要求 `resourceLimits`（cpu/memory），本服务将用户输入的无单位 CPU 核数与以 Gi
        为单位的内存整数转换为 Kubernetes 风格字符串（例如 `0.5` → `"0.5"`、`2` → `"2Gi"`）。
        默认值为 `{"cpu": "1", "memory": "1Gi"}`；调用方传入部分覆盖时保留另一项默认值。
        """
        payload = {"name": name}
        if metadata:
            payload.update(metadata)
        try:
            sandbox = SandboxSync.create(
                image,
                env=env,
                metadata=payload,
                resource={**_DEFAULT_RESOURCE_LIMITS, **(resource_limits or {})},
                timeout=None,
                connection_config=self._config,
                skip_health_check=skip_health_check,
                entrypoint=[
                    "/bin/sh",
                    "-c",
                    (
                        "if [ -x /root/.start.sh ]; then "
                        "exec /root/.start.sh /usr/sbin/sshd -D -e; "
                        "else "
                        "printf '%s\\n' '/root/.start.sh not found' >&2; "
                        "fi"
                    ),
                ],
            )
            container_id = sandbox.id
            sandbox.close()
            return CreatedSandbox(container_id=container_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("OpenSandbox 创建容器失败: %s: %s", type(exc).__name__, exc)
            raise OpenSandboxError("创建容器失败") from exc

    def get_status(self, container_id: str) -> SandboxStatus:
        """获取容器运行状态（v4 §8.2 Get Status）。"""
        info = self._run(container_id, "获取容器状态", lambda sb: sb.get_info())
        status = info.status
        return SandboxStatus(
            state=status.state,
            reason=status.reason,
            message=status.message,
            transitioned_at=(
                _to_timezone_iso(status.last_transition_at)
                if status.last_transition_at is not None
                else None
            ),
        )

    def get_endpoint(self, container_id: str, port: int) -> SandboxEndpoint:
        """获取容器外部访问端点（v4 §8.2 Get Endpoint）。"""
        endpoint = (self._run
                    (container_id,
                     "获取容器端点",
                     lambda sb: sb.get_endpoint(port))
                    )
        return SandboxEndpoint(
            endpoint=endpoint.endpoint,
            headers=dict(endpoint.headers or {})
        )

    def get_metrics(self, container_id: str) -> SandboxMetrics:
        """获取容器当前 CPU/内存使用率（百分比）。"""
        try:
            raw = self._get_metrics_raw(container_id)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "OpenSandbox 获取容器资源使用率失败: %s: %s: %s",
                container_id,
                type(exc).__name__,
                exc,
            )
            raise _classify(exc, "获取容器资源使用率失败") from exc
        try:
            cpu_usage = _metric_number(
                getattr(raw, "cpu_used_percentage", getattr(raw, "cpu_used_pct", None)),
                "CPU 使用率",
            )
            memory_total = _metric_number(
                getattr(raw, "memory_total_in_mib", getattr(raw, "mem_total_mib", None)),
                "内存总量",
            )
            memory_used = _metric_number(
                getattr(raw, "memory_used_in_mib", getattr(raw, "mem_used_mib", None)),
                "内存使用量",
            )
        except (TypeError, ValueError) as exc:
            logger.error("OpenSandbox 获取容器资源使用率失败: %s: %s: %s", container_id, type(exc).__name__, exc)
            raise OpenSandboxError("获取容器资源使用率失败") from exc
        memory_usage = (memory_used / memory_total * 100) if memory_total > 0 else 0.0
        return SandboxMetrics(cpu_usage=cpu_usage, memory_usage=memory_usage)

    def get_logs(self, container_id: str) -> str:
        """获取容器日志；固定请求 OpenSandbox 最后 10000 行。"""
        path = f"/sandboxes/{quote(str(container_id), safe='')}/diagnostics/logs"
        headers = {
            "User-Agent": self._config.user_agent,
            **self._config.headers,
            "Accept": "text/plain",
        }
        api_key = self._config.get_api_key()
        if api_key:
            headers["OPEN-SANDBOX-API-KEY"] = api_key

        try:
            with HttpxClient(
                base_url=self._config.get_base_url(),
                headers=headers,
                timeout=self._config.request_timeout.total_seconds(),
            ) as client:
                response = client.get(path, params={"tail": _LOG_TAIL})
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "OpenSandbox 获取容器日志请求失败: %s: %s: %s",
                container_id,
                type(exc).__name__,
                exc,
            )
            raise OpenSandboxError("获取容器日志失败") from exc

        if response.status_code == 404:
            logger.error("OpenSandbox 获取容器日志失败: %s: HTTP 404", container_id)
            raise SandboxNotFoundError("获取容器日志失败")
        if response.status_code != 200:
            logger.error(
                "OpenSandbox 获取容器日志失败: %s: HTTP %s",
                container_id,
                response.status_code,
            )
            raise OpenSandboxError("获取容器日志失败")
        return response.text

    def _get_metrics_raw(self, container_id: str) -> object:
        """通过直连 execd 端点获取 metrics；仅修正服务容器不可达的 loopback 主机。"""
        from opensandbox.constants import DEFAULT_EXECD_PORT
        from opensandbox.sync.adapters.factory import AdapterFactorySync

        factory = AdapterFactorySync(self._config)
        sandbox_service = factory.create_sandbox_service()
        metrics_service: object | None = None
        try:
            endpoint = sandbox_service.get_sandbox_endpoint(
                container_id,
                DEFAULT_EXECD_PORT,
                False,
            )
            endpoint.endpoint = _rewrite_loopback_endpoint(endpoint.endpoint)
            metrics_service = factory.create_metrics_service(endpoint)
            get_metrics = getattr(metrics_service, "get_metrics")
            return get_metrics(container_id)
        finally:
            _close_sdk_service(metrics_service)
            _close_sdk_service(sandbox_service)

    def start(self, container_id: str) -> None:
        """启动容器（v4 §11.3 Start 幂等；容器不存在视为已满足）。"""
        try:
            current = self.get_status(container_id)
        except SandboxNotFoundError:
            return
        except OpenSandboxError:
            # 状态查询失败通常表示 OpenSandbox 不可达；不要继续 resume，
            # 否则一次请求会触发多次连接异常并产生重复日志。
            raise
        if current is not None and current.state.strip().upper() == "RUNNING":
            return
        try:
            sandbox = SandboxSync.resume(
                container_id,
                connection_config=self._config,
                skip_health_check=True,
            )
            sandbox.close()
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc) or _is_in_state(self, container_id, frozenset({"RUNNING"})):
                return
            logger.error(
                "OpenSandbox 启动容器失败: %s: %s: %s",
                container_id,
                type(exc).__name__,
                exc,
            )
            raise OpenSandboxError("启动容器失败") from exc

    def stop(self, container_id: str) -> None:
        """停止容器（v4 §11.3 Stop 幂等；容器不存在视为已停止）。"""
        try:
            current = self.get_status(container_id)
        except SandboxNotFoundError:
            return
        except OpenSandboxError:
            # 连接失败时直接向上层报告后端故障，不再继续 pause。
            raise
        if current is not None and current.state.strip().upper() in _STOPPED_STATES:
            return
        try:
            self._run(container_id, "停止容器", lambda sb: sb.pause())
        except SandboxNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            if _is_in_state(self, container_id, _STOPPED_STATES):
                return
            logger.error(
                "OpenSandbox 停止容器失败: %s: %s: %s",
                container_id,
                type(exc).__name__,
                exc,
            )
            raise OpenSandboxError("停止容器失败") from exc

    def restart(self, container_id: str) -> None:
        """重启容器：停止并重新启动，Container ID 不变（v4 §11.3 Restart 幂等）。"""
        self.stop(container_id)
        self.start(container_id)

    def delete(self, container_id: str) -> None:
        """物理删除容器（v4 §11.4 Permanent Delete；已删除幂等成功）。"""
        try:
            sandbox = SandboxSync.connect(
                container_id,
                connection_config=self._config,
                skip_health_check=True,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return
            logger.error(
                "OpenSandbox 删除容器失败: %s: %s: %s",
                container_id,
                type(exc).__name__,
                exc,
            )
            raise OpenSandboxError("删除容器失败") from exc
        try:
            sandbox.destroy()
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return
            logger.error(
                "OpenSandbox 删除容器失败: %s: %s: %s",
                container_id,
                type(exc).__name__,
                exc,
            )
            raise OpenSandboxError("删除容器失败") from exc

    def _run(self, container_id: str, summary: str, op: Callable[[SandboxSync], _T]) -> _T:
        """连接容器执行 `op` 并释放本地资源；异常记录详细日志后抛摘要。

        容器不存在时抛 `SandboxNotFoundError`（错误细节仍写日志），其余异常抛 `OpenSandboxError`。
        """
        try:
            sandbox = SandboxSync.connect(
                container_id,
                connection_config=self._config,
                skip_health_check=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "OpenSandbox %s 失败（连接）: %s: %s: %s",
                summary,
                container_id,
                type(exc).__name__,
                exc,
            )
            raise _classify(exc, f"{summary}失败") from exc
        try:
            return op(sandbox)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "OpenSandbox %s 失败: %s: %s: %s",
                summary,
                container_id,
                type(exc).__name__,
                exc,
            )
            raise _classify(exc, f"{summary}失败") from exc
        finally:
            sandbox.close()


def _classify(exc: Exception, summary: str) -> OpenSandboxError:
    """按异常内容分类对外错误：容器不存在 → `SandboxNotFoundError`，其余 → `OpenSandboxError`。"""
    if _is_not_found(exc):
        return SandboxNotFoundError(summary)
    return OpenSandboxError(summary)


def _is_not_found(exc: Exception) -> bool:
    """SDK 抛错语义启发式判断「容器不存在」（跨版本稳定，见 [DOCKER::SANDBOX_NOT_FOUND]）。"""
    status_code = getattr(exc, "status_code", None)
    if status_code == 404 or status_code == "404":
        return True

    error = getattr(exc, "error", None)
    error_code = str(getattr(error, "code", "")).lower()
    if error_code in {"not_found", "sandbox_not_found", "sandbox-not-found"}:
        return True

    text = str(exc).lower()
    return (
        "not found" in text
        or "sandbox_not_found" in text
        or "sandbox not found" in text
        or "does not exist" in text
    )


def _to_timezone_iso(value: datetime) -> str:
    """将 OpenSandbox 时间统一转换为系统配置时区的 ISO 字符串。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_TIMEZONE).isoformat()


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _rewrite_loopback_endpoint(endpoint: str) -> str:
    """将 OpenSandbox 返回的 loopback execd 地址改为客户端可达的服务主机。"""
    parsed = urlsplit(endpoint)
    if not parsed.netloc:
        parsed = urlsplit(f"//{endpoint}")
    hostname = (parsed.hostname or "").lower()
    if hostname not in _LOOPBACK_HOSTS:
        return endpoint

    server_url = settings.opensandbox_url
    server = urlsplit(server_url if "://" in server_url else f"//{server_url}")
    replacement_host = server.hostname
    if not replacement_host:
        return endpoint
    try:
        port = parsed.port
    except ValueError:
        return endpoint

    if ":" in replacement_host and not replacement_host.startswith("["):
        replacement_host = f"[{replacement_host}]"
    netloc = replacement_host if port is None else f"{replacement_host}:{port}"
    return urlunsplit(("", netloc, parsed.path, parsed.query, parsed.fragment)).lstrip("/")


def _close_sdk_service(service: object | None) -> None:
    """释放官方 SDK 适配器持有的 HTTP 客户端。"""
    if service is None:
        return
    http_client = getattr(service, "_httpx_client", None)
    if isinstance(http_client, HttpxClient):
        http_client.close()


def _metric_number(value: object, label: str) -> float:
    """将 SDK 指标字段转换为有限非负数字。"""
    if value is None or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label}缺失")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label}非法: {value!r}") from exc
    if number < 0 or number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{label}非法: {value!r}")
    return number


def _is_in_state(client: OpenSandboxClient, container_id: str, states: frozenset[str]) -> bool:
    """操作失败后复核远端状态，避免把已达到目标状态的请求报为失败。"""
    try:
        status = client.get_status(container_id)
    except SandboxNotFoundError:
        return True
    except OpenSandboxError:
        return False
    return status.state.strip().upper() in states
