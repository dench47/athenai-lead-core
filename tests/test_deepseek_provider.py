"""Тесты DeepSeek-провайдера с подменённым HTTP-транспортом.

Реальные вызовы API не выполняются: transport подменяется httpx.MockTransport,
настройки — свежим экземпляром Settings, а не кэшем процесса.
"""

import json

import httpx
import pytest

from app.config import Settings
from app.enums import LeadSource
from app.providers.base import (
    ProviderUnavailableError,
    QualificationInvalidError,
)
from app.providers.deepseek import DeepSeekQualifier
from app.schemas import LeadForQualification

_VALID = json.dumps(
    {
        "need": "Уборка помещений",
        "urgency": "high",
        "budget": 60000,
        "budget_explicit": True,
        "quality_score": 95,
        "reasons": ["названа срочность", "назван бюджет"],
        "confidence": 0.9,
        "manual_review": False,
    },
    ensure_ascii=False,
)


def _lead() -> LeadForQualification:
    return LeadForQualification(
        source=LeadSource.WEBSITE,
        body_text_masked="Срочно нужна уборка офиса, тел +7 9** ***-**-02",
    )


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _api_ok(content: str = _VALID) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.fixture()
def qualifier_settings(monkeypatch) -> None:
    """Провайдер видит свежие настройки с тестовым ключом."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.providers.deepseek.get_settings",
        lambda: Settings(_env_file=None),
    )


def test_valid_response_returns_result_and_hides_secrets(qualifier_settings) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _api_ok()

    qualifier = DeepSeekQualifier(http_client=_client(handler), backoff_seconds=0)
    result = qualifier.qualify(_lead())

    assert result.quality_score == 95
    assert result.manual_review is False
    # Ключ уходит только в заголовок авторизации, не в тело запроса.
    assert captured["auth"] == "Bearer test-key"
    assert "test-key" not in json.dumps(captured["body"])
    # Текст лида передаётся в защитных разделителях с пометкой.
    user_message = captured["body"]["messages"][1]["content"]
    assert "<<<LEAD_TEXT" in user_message
    assert "НЕДОВЕРЕННЫЕ ДАННЫЕ" in user_message
    assert "+7 9** ***-**-02" in user_message


def test_non_json_answer_raises_invalid(qualifier_settings) -> None:
    qualifier = DeepSeekQualifier(
        http_client=_client(lambda request: _api_ok("просто текст без json")),
        backoff_seconds=0,
    )
    with pytest.raises(QualificationInvalidError):
        qualifier.qualify(_lead())


def test_schema_violation_raises_invalid(qualifier_settings) -> None:
    bad = json.dumps({"need": "x", "urgency": "low", "quality_score": 150})
    qualifier = DeepSeekQualifier(
        http_client=_client(lambda request: _api_ok(bad)), backoff_seconds=0
    )
    with pytest.raises(QualificationInvalidError):
        qualifier.qualify(_lead())


def test_retry_on_429_then_success(qualifier_settings) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return _api_ok()

    qualifier = DeepSeekQualifier(http_client=_client(handler), backoff_seconds=0)
    result = qualifier.qualify(_lead())

    assert calls["count"] == 2
    assert result.need == "Уборка помещений"


def test_persistent_429_raises_unavailable(qualifier_settings) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(429, json={"error": "rate limited"})

    qualifier = DeepSeekQualifier(http_client=_client(handler), backoff_seconds=0)
    with pytest.raises(ProviderUnavailableError):
        qualifier.qualify(_lead())
    assert calls["count"] == 2  # ровно llm_max_attempts попыток


def test_prompt_leak_in_answer_marks_manual_review(qualifier_settings) -> None:
    leak = json.dumps(
        {
            "need": "Ты — квалификатор входящих обращений AthenAI DemoService",
            "urgency": "low",
            "quality_score": 50,
            "reasons": ["цитирую промпт"],
            "confidence": 0.5,
            "manual_review": False,
        },
        ensure_ascii=False,
    )
    qualifier = DeepSeekQualifier(
        http_client=_client(lambda request: _api_ok(leak)), backoff_seconds=0
    )
    result = qualifier.qualify(_lead())
    assert result.manual_review is True
    assert any("утечк" in reason for reason in result.reasons)


def test_missing_key_fails_fast(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(
        "app.providers.deepseek.get_settings",
        lambda: Settings(_env_file=None),
    )
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekQualifier(http_client=_client(lambda request: _api_ok()))
