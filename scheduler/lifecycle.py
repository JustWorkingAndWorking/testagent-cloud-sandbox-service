"""
后台调度服务（v4 §13）。

- 单实例、独立于 REST 请求运行；由 `run_loop` 定时编排。
- **各类检查全部独立成可调用函数**（`expire_containers` / `purge_containers` /
  `refresh_status_cache`），可由管理 API 读取缓存；循环仅负责按周期编排。
- 调度扫描按业务状态排除已完成记录，重复扫描无副作用（v4 §13.2）。
- 运行状态写入进程内内存缓存（不写 SQLite），供管理 API 展示（v4 §13.2/§8.3）。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from config import Constants, settings
from application import container as _container
from domain.models import ContainerStatus, add_hours_to_iso, map_runtime_state
from infra.db import session_scope
from infra.opensandbox.client import SandboxNotFoundError
from infra.repositories import ContainerRepository

logger = logging.getLogger(__name__)

__all__ = [
    "expire_containers",
    "purge_containers",
    "refresh_status_cache",
    "compensate",
    "run_loop",
    "get_cached_status",
    "all_cached_statuses",
]

_TZ = ZoneInfo(Constants.TIMEZONE.value)

#: 进程内运行状态缓存：container_id -> 业务状态（v4 §13.2；管理 API 读取，不落库）
_status_cache: dict[str, ContainerStatus] = {}


def _now() -> datetime:
    return datetime.now(_TZ)


# ---------------------------------------------------------------------------
# T7.2 业务过期检查（独立；可被管理 API 调用）
# ---------------------------------------------------------------------------
def expire_containers() -> list[str]:
    """`created_at + expiration_hours` 到期的活跃容器执行业务删除（Stop + 写 deleted_at）。

    - `expiration_hours == 0`（永不过期）跳过；幂等（已业务删除的不再处理）。
    - 单容器失败记录日志并继续，不影响整体扫描。
    - 返回本次处理的容器 ID 列表。
    """
    expired: list[str] = []

    # 只收集候选 ID；每个候选在生命周期锁内用新事务重新读取。
    with session_scope() as session:
        candidate_ids = [
            row.container_id for row in ContainerRepository(session).list_active()
        ]

    for container_id in candidate_ids:
        with _container.lifecycle_guard():
            with session_scope() as session:
                # 新 Session 避免使用首轮扫描的 expiration_hours/created_at 快照。
                current = ContainerRepository(session).get(container_id)
                if current is None or current.deleted_at is not None:
                    continue
                if current.expiration_hours <= 0:
                    continue
                expires = add_hours_to_iso(current.created_at, current.expiration_hours)
                if expires is None or datetime.fromisoformat(expires) > _now():
                    continue
            # noinspection broad-exception
            try:
                _container.business_delete(container_id)
                expired.append(container_id)
            except Exception:  # noqa: BLE001
                logger.exception("业务过期处理失败: %s", container_id)
    if expired:
        logger.info("业务过期检查: 业务删除 %d 个", len(expired))
    return expired


# ---------------------------------------------------------------------------
# T7.3 保留期检查（独立；物理删除前二次核对 deleted_at 防与管理 API 恢复竞争）
# ---------------------------------------------------------------------------
def purge_containers() -> list[str]:
    """`deleted_at + TA_SS_CONTAINER_RETENTION_HOURS` 到期即物理删除
    （OpenSandbox Delete + 删除 SQLite 记录）。

    - `retention_hours <= 0` 视为永不物理删除。
    - 物理删除前在事务内二次核对 `deleted_at`（若已被管理 API 恢复/清除则跳过），
      并在同一事务内执行外部删除与记录删除，借助 SQLite 写锁与管理 API 恢复互斥，避免竞争。
    - 返回本次物理删除的容器 ID 列表。
    """
    retention_hours = settings.container_retention_hours
    if retention_hours <= 0:
        return []
    purged: list[str] = []

    # 只收集候选 ID；每个候选在生命周期锁内用新事务重新读取。
    with session_scope() as session:
        candidate_ids = [
            row.container_id
            for row in ContainerRepository(session).list_all()
            if row.deleted_at is not None
        ]

    for container_id in candidate_ids:
        with _container.lifecycle_guard():
            with session_scope() as session:
                repo = ContainerRepository(session)
                # 新 Session 避免复用首轮扫描的 identity map 快照。
                current = repo.get(container_id)
                if current is None:
                    continue
                if current.deleted_at is None:
                    continue
                deadline = datetime.fromisoformat(current.deleted_at) + timedelta(
                    hours=retention_hours
                )
                if deadline > _now():
                    continue
                # noinspection broad-exception
                try:
                    _container.get_opensandbox_client().delete(container_id)
                except Exception:  # noqa: BLE001
                    logger.exception("保留期物理删除失败 (外部删除） %s", container_id)
                    continue
                repo.delete(container_id)
                _status_cache.pop(container_id, None)
                purged.append(container_id)
    if purged:
        logger.info("保留期检查: 物理删除 %d 个", len(purged))
    return purged


# ---------------------------------------------------------------------------
# T7.4 运行状态刷新（独立；写内存缓存，供管理 API 展示）
# ---------------------------------------------------------------------------
def refresh_status_cache() -> None:
    """刷新全部活跃容器的运行状态到进程内缓存；清理已不存在容器的缓存项。

    状态映射（v4 §8.3）：运行 → `running`；已停止 → `stopped`；创建/重启期间 → `pending`；
    不可达 → `unknown`；sandbox 已消失 → `stopped`。
    """
    with session_scope() as session:
        rows = list(ContainerRepository(session).list_active())
    active_ids: set[str] = set()
    for row in rows:
        active_ids.add(row.container_id)
        _status_cache[row.container_id] = _fetch_status(row.container_id)
    stale = [cid for cid in _status_cache if cid not in active_ids]
    for cid in stale:
        _status_cache.pop(cid, None)
    if rows or stale:
        logger.info("状态刷新: 更新 %d 个容器状态，清理 %d 个失效项", len(rows), len(stale))


def _fetch_status(container_id: str) -> ContainerStatus:
    # noinspection broad-exception
    try:
        status = _container.get_opensandbox_client().get_status(container_id)
    except SandboxNotFoundError:
        return ContainerStatus.STOPPED  # 与状态查询（T6.7）语义一致
    except Exception:  # noqa: BLE001
        logger.exception("状态刷新失败: %s", container_id)
        return ContainerStatus.UNKNOWN
    return map_runtime_state(status.state)


def get_cached_status(container_id: str) -> Optional[ContainerStatus]:
    """读取进程内缓存的状态（管理 API 展示用）；无缓存返回 None。"""
    return _status_cache.get(container_id)


def all_cached_statuses() -> dict[str, ContainerStatus]:
    """返回全部缓存状态快照（管理 API 展示用）。"""
    return dict(_status_cache)


# ---------------------------------------------------------------------------
# T7.5 补偿
# ---------------------------------------------------------------------------
def compensate() -> list[str]:
    """进程重启后的首次补齐：扫描全部持久化记录，补齐到期的业务删除与保留期物理删除。

    幂等：扫描范围排除已完成动作，不重复触发业务删除/物理删除。
    """
    done = expire_containers()
    done += purge_containers()
    if done:
        logger.info("补偿完成：处理 %d 项", len(done))
    return done


# ---------------------------------------------------------------------------
# T7.1 定时循环
# ---------------------------------------------------------------------------
def run_loop(stop_event: threading.Event) -> None:
    """单实例后台循环：先补偿一次，再按周期执行过期 / 保留期 / 状态刷新检查。

    周期：`TA_SS_SCHEDULER_POLL_INTERVAL_SECONDS`（v4 §13.1）。
    """
    # noinspection broad-exception
    try:
        compensate()
    except Exception:  # noqa: BLE001
        logger.exception("启动后初次调度失败")
    interval = settings.scheduler_poll_interval_seconds
    steps = (expire_containers, purge_containers, refresh_status_cache)
    while not stop_event.wait(interval):
        for step in steps:
            # noinspection broad-exception
            try:
                step()
            except Exception:  # noqa: BLE001
                logger.exception("调度检查异常: %s", step.__name__)
