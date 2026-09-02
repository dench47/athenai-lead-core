"""Контракт провайдеров квалификации.

Оба провайдера (детерминированный mock и LLM) принимают на вход
LeadForQualification и обязаны вернуть QualificationResult, проходящий
строгую схему. Ответ мимо схемы — это QualificationInvalidError,
а не «лучше, чем ничего»: такой кейс уходит на ручную проверку.
"""

from typing import Protocol

from app.schemas import LeadForQualification, QualificationResult


class QualificationInvalidError(Exception):
    """Провайдер вернул ответ, не проходящий строгую схему.

    Модель ОТВЕТИЛА, но мусором: кейс уходит на ручную проверку.
    """


class ProviderUnavailableError(Exception):
    """Провайдер недоступен (таймаут, 429, 5xx) после всех повторов.

    Это не вина модели: кейс остаётся в new и будет повторён.
    """


class QualificationProvider(Protocol):
    name: str

    def qualify(self, lead: LeadForQualification) -> QualificationResult: ...
