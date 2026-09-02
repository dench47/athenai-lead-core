"""Детерминированный mock-квалификатор.

Заменяет LLM 1-в-1 по контракту QualificationResult и работает без ключей:
одинаковый текст всегда даёт одинаковую оценку — это делает маршруты
предсказуемыми и воспроизводимыми для проверяющего.

Правила прозрачные (для защиты): срочность — по словам, бюджет — только
явно названная сумма, score — аддитивные баллы за признаки. Признаки
prompt injection уводят кейс на ручную проверку.
"""

import re
from decimal import Decimal

from app.enums import Urgency
from app.schemas import LeadForQualification, QualificationResult

_URGENT_MARKERS = ("срочн", "сегодня", "до завтра", "завтра нужно", "asap", "немедленн")
_MEDIUM_URGENCY_MARKERS = ("на этой неделе", "на следующей неделе", "до конца месяца")
_SPECIFIC_MARKERS = (
    "офис", "квартир", "после ремонта", "переезд", "регулярн",
    "коттедж", "помещени", "склад", "уборк", "клининг",
)
_AREA_MARKERS = ("м2", "м²", "кв.м", "кв м")
_NON_TARGET_MARKERS = ("ваканс", "резюме", "seo", "реклам", "казин", "крипт", "стажиров")

# Попытки заставить «ИИ-сотрудника» изменить правила. Входящий текст —
# недоверенные данные, такие фразы не исполняются, а флагаются.
_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "ignore the above",
    "игнорируй инструкц",
    "игнорируй предыдущ",
    "игнорируй правила",
    "забудь инструкц",
    "забудь все",
    "system prompt",
    "твой промпт",
    "раскрой промпт",
    "покажи промпт",
    "выведи инструкц",
    "developer message",
    "ты теперь",
)

_AMOUNT_RE = re.compile(
    r"(\d[\d\u00a0 .,]{0,10})\s*(тысяч|тыс|рублей|руб|р\.|₽|k|к)(?![а-яa-z])",
    re.IGNORECASE,
)
_THOUSAND_UNITS = ("тысяч", "тыс", "k", "к")

_NEED_RULES = (
    (("уборк", "клининг", "убрать"), "Уборка помещений"),
    (("ремонт", "отделк"), "Ремонт / отделка"),
    (("переезд", "перевезти"), "Переезд / логистика"),
)


class MockQualifier:
    name = "mock"

    def qualify(self, lead: LeadForQualification) -> QualificationResult:
        text = lead.body_text_masked.strip()
        lowered = text.lower()

        if len(text) < 15:
            return self._manual(
                need="Не определена",
                reason="Обращение слишком короткое для автоматической оценки",
            )
        if any(marker in lowered for marker in _INJECTION_MARKERS):
            return self._manual(
                need="Подозрение на prompt injection",
                reason=(
                    "Признаки prompt injection: попытка изменить инструкции системы. "
                    "Кейс передан человеку, команды из текста не исполняются"
                ),
            )

        if any(marker in lowered for marker in _NON_TARGET_MARKERS):
            return QualificationResult(
                need="Похоже на нецелевое обращение",
                urgency=Urgency.LOW,
                quality_score=10,
                reasons=["Текст похож на нецелевое (вакансии, реклама, спам)"],
                confidence=0.9,
                manual_review=False,
            )

        reasons: list[str] = []
        score = 25

        urgency = Urgency.LOW
        if any(marker in lowered for marker in _URGENT_MARKERS):
            urgency = Urgency.HIGH
            score += 25
            reasons.append("Клиент называет срочность")
        elif any(marker in lowered for marker in _MEDIUM_URGENCY_MARKERS):
            urgency = Urgency.MEDIUM
            score += 10
            reasons.append("Клиент называет горизонт: дни-недели")

        budget, budget_explicit = self._extract_budget(lowered)
        if budget_explicit:
            score += 25
            reasons.append("Клиент явно назвал бюджет")

        if any(marker in lowered for marker in _SPECIFIC_MARKERS):
            score += 15
            reasons.append("Есть конкретика задачи (тип помещения / работ)")
        if any(marker in lowered for marker in _AREA_MARKERS):
            score += 10
            reasons.append("Указана площадь или объём")
        if len(text) > 80:
            score += 10
            reasons.append("Развёрнутый текст — контекст понятен")

        return QualificationResult(
            need=self._derive_need(lowered),
            urgency=urgency,
            budget=budget,
            budget_explicit=budget_explicit,
            quality_score=min(score, 100),
            reasons=reasons or ["Признаков качества мало, оценка базовая"],
            confidence=0.9,
            manual_review=False,
        )

    def _manual(self, need: str, reason: str) -> QualificationResult:
        return QualificationResult(
            need=need,
            urgency=Urgency.LOW,
            quality_score=0,
            reasons=[reason],
            confidence=1.0,
            manual_review=True,
        )

    def _derive_need(self, lowered: str) -> str:
        for keywords, need in _NEED_RULES:
            if any(keyword in lowered for keyword in keywords):
                return need
        return "Не определена — задать уточняющий вопрос"

    def _extract_budget(self, lowered: str) -> tuple[Decimal | None, bool]:
        match = _AMOUNT_RE.search(lowered)
        if match is None:
            return None, False
        raw_number, unit = match.group(1), match.group(2).lower()
        cleaned = raw_number.replace(" ", "").replace("\u00a0", "").replace(",", ".")
        try:
            value = float(cleaned)
        except ValueError:
            return None, False
        if unit in _THOUSAND_UNITS:
            value *= 1000
        return Decimal(str(round(value, 2))), True
