"""
OpenSandbox 集成层（v4 §8）。

- 使用 OpenSandbox 官方 Python 包（同步封装 `SandboxSync`）作为客户端，封装于本模块；
  业务层与接口层不直接依赖原始包细节（v4 §8.1）。
- 连接地址与可选 API Key 来自环境变量（v4 §5.1 `TA_SS_OPENSANDBOX_*`）；未设置 Key 不发送（v4 §8.4）。
- 调用失败或不可达时 MUST 将底层详细错误写日志，对外只抛合理摘要（v4 §8.4）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Optional, TypeVar

from opensandbox.config.connection_sync import ConnectionConfigSync
from opensandbox.sync.sandbox import SandboxSync

from config import settings
from infra.opensandbox.types import CreatedSandbox, SandboxEndpoint, SandboxStatus

logger = logging.getLogger(__name__)

__all__ = [
    "OpenSandboxClient",
    "OpenSandboxError",
    "SandboxNotFoundError",
]

#: 默认资源限额（服务端创建沙箱必需 `resourceLimits` 字段）
_DEFAULT_RESOURCE_LIMITS: dict[str, str] = {"cpu": "1", "memory": "1Gi"}

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
        skip_health_check: bool = False,
        resource_limits: Optional[dict[str, str]] = None,
    ) -> CreatedSandbox:
        """创建并自动启动容器（v4 §11.1）；`timeout=None` 表示生命周期由本服务管理。

        服务端要求 `resourceLimits`（cpu/memory），默认 `{"cpu": "1", "memory": "1Gi"}`。
        """
        payload = {"name": name}
        if metadata:
            payload.update(metadata)
        try:
            sandbox = SandboxSync.create(
                image,
                env=env,
                metadata=payload,
                resource=resource_limits or _DEFAULT_RESOURCE_LIMITS,
                timeout=None,
                connection_config=self._config,
                skip_health_check=skip_health_check,
            )
            container_id = sandbox.id
            sandbox.close()
            return CreatedSandbox(container_id=container_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("OpenSandbox 创建容器失败")
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
                status.last_transition_at.isoformat()
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

    def start(self, container_id: str) -> None:
        """启动容器（v4 §11.3 Start 幂等；容器不存在视为已满足）。"""
        try:
            sandbox = SandboxSync.resume(
                container_id,
                connection_config=self._config,
                skip_health_check=True,
            )
            sandbox.close()
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return
            logger.exception("OpenSandbox 启动容器失败: %s", container_id)
            raise OpenSandboxError("启动容器失败") from exc

    def stop(self, container_id: str) -> None:
        """停止容器（v4 §11.3 Stop 幂等；容器不存在视为已停止）。"""
        try:
            self._run(container_id, "停止容器", lambda sb: sb.pause())
        except SandboxNotFoundError:
            pass

    def kill(self, container_id: str) -> None:
        """强制终止容器（v4 §11.3 Kill 幂等；容器不存在视为已终止）。"""
        try:
            self._run(container_id, "强制终止容器", lambda sb: sb.kill())
        except SandboxNotFoundError:
            pass

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
            logger.exception("OpenSandbox 删除容器失败: %s", container_id)
            raise OpenSandboxError("删除容器失败") from exc
        try:
            sandbox.destroy()
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return
            logger.exception("OpenSandbox 删除容器失败: %s", container_id)
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
            logger.exception("OpenSandbox %s 失败（连接）: %s", summary, container_id)
            raise _classify(exc, f"{summary}失败") from exc
        try:
            return op(sandbox)
        except Exception as exc:  # noqa: BLE001
            logger.exception("OpenSandbox %s 失败: %s", summary, container_id)
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
    text = str(exc).lower()
    return "not found" in text or "sandbox_not_found" in text
