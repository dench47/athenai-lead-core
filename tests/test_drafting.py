"""Тесты генераторов черновиков: mock-шаблоны и DeepSeek (транспорт подменён)."""

import json

import httpx
import pytest

from app.config import Settings
from app.enums import LeadSource, Urgency
from app.guardrails import check_draft
from app.providers.base import ProviderUnavailableError, QualificationInvalidError
from app.providers.drafting import DeepSeekDrafter, MockDrafter
from app.schemas import DraftInput, DraftResult


def _input(need: str = "Уборка помещений", urgency: Urgency = Urgency.HIGH) -> DraftInput:
    return DraftInput(
        source=LeadSource.WEBSITE,
        need=need,
        urgency=urgency,
        budget_explicit=True,
        body_text_masked="Нужна уборка офиса после ремонта",
    )


def test_mock_draft_always_passes_guardrails() -> None:
    """Ключевое свойство: шаблонный черновик не содержит запрещённого."""
    for need in ("Уборка помещений", "Ремонт / отделка", "Переезд / логистика",
                 "Не определена — задать уточняющий вопрос"):
        for urgency in (Urgency.HIGH, Urgency.MEDIUM, Urgency.LOW):
            draft = MockDrafter().compose(_input(need, urgency))
            assert check_draft(draft) == [], (need, urgency)


def test_mock_draft_mentions_need_and_urgency() -> None:
    draft = MockDrafter().compose(_input())
    assert "Уборка помещений" in draft.reply_text
    assert "срочным" in draft.reply_text
    assert draft.clarifying_question.startswith("Какая площадь")
    assert "бюджет" in draft.next_action  # напоминание менеджеру без цифр


def test_mock_drafter_is_deterministic() -> None:
    first = MockDrafter().compose(_input())
    second = MockDrafter().compose(_input())
    assert first == second


_VALID_DRAFT = json.dumps(
    {
        "reply_text": (
            "Здравствуйте! Спасибо за обращение. Запрос уже в работе, "
            "менеджер свяжется с вами для уточнения деталей."
        ),
        "clarifying_question": "Какая площадь помещения?",
        "next_action": "Позвонить клиенту и назначить осмотр.",
    },
    ensure_ascii=False,
)


@pytest.fixture()
def drafter_settings(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.providers.drafting.get_settings",
        lambda: Settings(_env_file=None),
    )


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_deepseek_drafter_valid_response(drafter_settings) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": _VALID_DRAFT}}]}
        )

    drafter = DeepSeekDrafter(http_client=_client(handler), backoff_seconds=0)
    draft = drafter.compose(_input())

    assert isinstance(draft, DraftResult)
    user_message = captured["body"]["messages"][1]["content"]
    assert "<<<LEAD_TEXT" in user_message
    assert "НЕДОВЕРЕННЫЕ ДАННЫЕ" in user_message


def test_deepseek_drafter_non_json_raises_invalid(drafter_settings) -> None:
    drafter = DeepSeekDrafter(
        http_client=_client(
            lambda request: httpx.Response(
                200, json={"choices": [{"message": {"content": "не json"}}]}
            )
        ),
        backoff_seconds=0,
    )
    with pytest.raises(QualificationInvalidError):
        drafter.compose(_input())


def test_deepseek_drafter_persistent_429_unavailable(drafter_settings) -> None:
    drafter = DeepSeekDrafter(
        http_client=_client(
            lambda request: httpx.Response(429, json={"error": "rate"})
        ),
        backoff_seconds=0,
    )
    with pytest.raises(ProviderUnavailableError):
        drafter.compose(_input())
