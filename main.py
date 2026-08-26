"""
进程入口（T10.4：REST API + Scheduler）。

- 环境变量由外部调用方注入（compose / shell 等，本进程不加载 .env）；
  导入 `config` 即触发必填环境变量校验，缺失即启动失败（v4 §5.1）。
- 初始化 SQLite（幂等建表），启动单实例 Scheduler 后台循环（v4 §13.1）。
- 以 uvicorn 承载 REST API（`0.0.0.0:{TA_SS_REST_API_PORT}`，默认 8080）；本进程仅装配 REST API 与 Scheduler。
"""

import logging
import sys
import threading

# noinspection unused-imports
import config  # noqa: E402  导入即校验 TA_SS_* 环境变量
from config import settings  # noqa: E402
from infra.db import init_db  # noqa: E402
from scheduler.lifecycle import run_loop  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger("main")
    logger.info("初始化数据库中...")
    init_db()

    stop_event = threading.Event()
    scheduler_thread = threading.Thread(
        target=run_loop, args=(stop_event,), daemon=True, name="scheduler"
    )
    scheduler_thread.start()
    logger.info("调度服务已启动 (调度周期 %ss)", settings.scheduler_poll_interval_seconds)

    logger.info("REST API 启动于: http://127.0.0.1:%s", settings.rest_api_port)
    import uvicorn

    from interfaces.app import create_app  # noqa: PLC0415

    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=settings.rest_api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
