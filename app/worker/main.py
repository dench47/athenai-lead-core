"""Точка входа воркера: фоновый цикл конвейера обработки лидов."""

import time

import structlog

from app.config import get_settings
from app.db import SessionLocal
from app.logging_setup import configure_logging
from app.worker.pipeline import run_once

configure_logging("worker")
log = structlog.get_logger("worker")

HEARTBEAT_EVERY_N_LOOPS = 30


def main() -> None:
    settings = get_settings()
    log.info("worker_started", poll_interval=settings.worker_poll_interval_seconds)

    loop_count = 0
    while True:
        try:
            with SessionLocal() as session:
                stats = run_once(session)
            if stats["claimed"]:
                log.info("pipeline_batch_done", **stats)
        except Exception:
            log.exception("worker_loop_failed")

        loop_count += 1
        if loop_count % HEARTBEAT_EVERY_N_LOOPS == 0:
            log.info("worker_heartbeat", loops=loop_count)
        time.sleep(settings.worker_poll_interval_seconds)


if __name__ == "__main__":
    main()
