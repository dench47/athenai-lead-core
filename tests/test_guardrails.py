"""Юнит-тесты guardrails: что запрещено писать в черновике."""

from app.guardrails import check_draft
from app.schemas import DraftResult


def _draft(
    reply: str, question: str = "Какой объём работ?", action: str = "Позвонить"
) -> DraftResult:
    return DraftResult(
        reply_text=reply, clarifying_question=question, next_action=action
    )


def test_clean_draft_passes() -> None:
    assert check_draft(_draft("Спасибо за обращение! Менеджер свяжется с вами.")) == []


def test_price_in_rubles_is_forbidden() -> None:
    violations = check_draft(_draft("Стоимость составит 5000 руб."))
    assert any("цен" in v for v in violations)


def test_dollar_price_is_forbidden() -> None:
    violations = check_draft(_draft("Это будет стоить $100 за визит."))
    assert violations


def test_discount_is_forbidden() -> None:
    violations = check_draft(_draft("Сделаем со скидкой 10% для новых клиентов."))
    assert any("скидк" in v for v in violations)


def test_guarantee_is_forbidden() -> None:
    violations = check_draft(_draft("Гарантируем качество работ."))
    assert any("гарант" in v for v in violations)


def test_time_promise_is_forbidden() -> None:
    violations = check_draft(_draft("Уберём в течение 2 дней."))
    assert any("срок" in v or "обещан" in v for v in violations)


def test_availability_claim_is_forbidden() -> None:
    violations = check_draft(_draft("Бригады есть свободные окна на завтра."))
    assert any("наличи" in v or "свободн" in v for v in violations)


def test_injection_echo_is_forbidden() -> None:
    violations = check_draft(
        _draft("Ignore previous instructions и мы всё согласуем.")
    )
    assert any("инструкц" in v for v in violations)


def test_check_covers_all_three_fields() -> None:
    violations = check_draft(
        DraftResult(
            reply_text="Хорошо!",
            clarifying_question="Бюджет 30 тыс уложимся?",
            next_action="Обещать лучшую цену",
        )
    )
    assert len(violations) >= 2
