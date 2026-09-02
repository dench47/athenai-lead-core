"""Точка входа воркера: фоновый конвейер обработки лидов."""

import time

import structlog

from app.config import get_settings
from app.logging_setup import configure_logging

configure_logging("worker")
log = structlog.get_logger("worker")

HEARTBEAT_EVERY_N_LOOPS = 15


def main() -> None:
    settings = get_settings()
    log.info("worker_started", poll_interval=settings.worker_poll_interval_seconds)

    loop_count = 0
    while True:
        # TODO: конвейер обработки (квалификация -> черновик -> CRM-синк).
        loop_count += 1
        if loop_count % HEARTBEAT_EVERY_N_LOOPS == 0:
            log.info("worker_heartbeat", loops=loop_count)
        time.sleep(settings.worker_poll_interval_seconds)


if __name__ == "__main__":
    main()
