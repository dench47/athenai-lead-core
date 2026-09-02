"""Структурированные логи: каждая строка — один JSON-объект.

Машиночитаемый формат позволяет фильтровать логи и строить метрики
без парсинга свободного текста.
"""

import logging

import structlog


def configure_logging(service: str, level: int = logging.INFO) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)
