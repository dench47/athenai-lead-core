"""Guardrails: детерминированная проверка черновика до показа человеку.

Правило задания: AI-агент не имеет права самостоятельно придумывать цены,
скидки, наличие, сроки и гарантии. Проверка не верит провайдеру на слово —
любой сгенерированный текст проходит через эти фильтры, нарушение уводит
кейс на ручную проверку.
"""

import re

from app.providers.mock import _INJECTION_MARKERS
from app.schemas import DraftResult

# Сумма с валютой в любом написании: «5000 руб», «50 тыс», «$100», «100 €».
_PRICE_RE = re.compile(
    r"(\d[\d\u00a0 .,]{0,10}\s*(руб|рубл|₽|тыс|k|к)(?![а-яa-z]))"
    r"|([$€]\s*\d)"
    r"|(\d\s*(\$|€))",
    re.IGNORECASE,
)

_DISCOUNT_WORDS = ("скидк", "дисконт", "акци", "спецпредлож", "промокод", "промо-код")
_PROMISE_WORDS = (
    "гарант",
    "обеща",
    "успеем",
    "гарантированн",
    "обязательно сделаем",
    "в течение",
    "точно к ",
    "к завтра",
)
_AVAILABILITY_WORDS = ("в наличии", "есть свободн", "свободные окна", "точно есть")

GUARDRAIL_CHECKS = (
    "цены/суммы",
    "скидки/акции",
    "сроки/обещания/гарантии",
    "наличие",
    "пересказ инструкций",
)


def check_draft(draft: DraftResult) -> list[str]:
    """Возвращает список нарушений; пустой список — черновик чист."""
    violations: list[str] = []
    fields = {
        "reply_text": draft.reply_text,
        "clarifying_question": draft.clarifying_question,
        "next_action": draft.next_action,
    }
    for field_name, text in fields.items():
        lowered = text.lower()
        if _PRICE_RE.search(text):
            violations.append(field_name + ": упоминание цены или суммы")
        if any(word in lowered for word in _DISCOUNT_WORDS):
            violations.append(field_name + ": скидки или акции")
        if any(word in lowered for word in _PROMISE_WORDS):
            violations.append(field_name + ": сроки, обещания или гарантии")
        if any(word in lowered for word in _AVAILABILITY_WORDS):
            violations.append(field_name + ": утверждение о наличии")
        if any(marker in lowered for marker in _INJECTION_MARKERS):
            violations.append(field_name + ": пересказ инструкций из обращения")
    return violations
