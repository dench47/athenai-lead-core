"""Провайдеры ИИ-конвейера: квалификация и черновики.

Переключатель один — QUALIFIER_PROVIDER (mock | deepseek): меняется весь
ИИ-слой целиком, контракты обоих провайдеров идентичны в обоих режимах.
"""

from app.config import get_settings
from app.providers.base import (
    Drafter,
    ProviderUnavailableError,
    QualificationInvalidError,
    QualificationProvider,
)
from app.providers.drafting import MockDrafter
from app.providers.mock import MockQualifier

__all__ = [
    "Drafter",
    "MockDrafter",
    "MockQualifier",
    "ProviderUnavailableError",
    "QualificationInvalidError",
    "QualificationProvider",
    "get_drafter",
    "get_qualifier",
]


def _provider_name() -> str:
    name = get_settings().qualifier_provider
    if name not in ("mock", "deepseek"):
        raise ValueError("unknown qualifier provider: " + name)
    return name


def get_qualifier() -> QualificationProvider:
    if _provider_name() == "deepseek":
        from app.providers.deepseek import DeepSeekQualifier

        return DeepSeekQualifier()
    return MockQualifier()


def get_drafter() -> Drafter:
    if _provider_name() == "deepseek":
        from app.providers.drafting import DeepSeekDrafter

        return DeepSeekDrafter()
    return MockDrafter()
