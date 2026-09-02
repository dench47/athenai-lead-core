"""Провайдеры квалификации: выбор по настройке QUALIFIER_PROVIDER."""

from app.config import get_settings
from app.providers.base import (
    ProviderUnavailableError,
    QualificationInvalidError,
    QualificationProvider,
)
from app.providers.mock import MockQualifier

__all__ = [
    "MockQualifier",
    "ProviderUnavailableError",
    "QualificationInvalidError",
    "QualificationProvider",
    "get_qualifier",
]


def get_qualifier() -> QualificationProvider:
    name = get_settings().qualifier_provider
    if name == "mock":
        return MockQualifier()
    if name == "deepseek":
        from app.providers.deepseek import DeepSeekQualifier

        return DeepSeekQualifier()
    raise ValueError("unknown qualifier provider: " + name)
