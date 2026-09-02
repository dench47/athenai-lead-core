"""Интеграционные тесты приёма событий: дедуп, invalid, opt-out, авторизация."""

import json

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.enums import CaseStatus, ConsentStatus
from app.models import LeadCase, LeadEvent


def _tg_payload(update_id: int = 5001, text: str = "Нужна уборка офиса 120 м2") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 42,
            "date": 1756800000,
            "from": {"id": 9001, "first_name": "Иван"},
            "text": text,
        },
    }


def _headers(source: str) -> dict:
    settings = get_settings()
    tokens = {
        "telegram": settings.webhook_token_telegram,
        "website": settings.webhook_token_website,
        "avito": settings.webhook_token_avito,
    }
    return {"X-Webhook-Token": tokens[source]}


def _all_cases() -> list[LeadCase]:
    with SessionLocal() as session:
        return list(session.execute(select(LeadCase)).scalars())


def _all_events() -> list[LeadEvent]:
    with SessionLocal() as session:
        return list(session.execute(select(LeadEvent)).scalars())


def test_duplicate_event_creates_single_case(client) -> None:
    """Обязательный сценарий: повтор external_event_id — один LeadCase."""
    first = client.post("/webhooks/telegram", json=_tg_payload(),
                        headers=_headers("telegram"))
    second = client.post("/webhooks/telegram", json=_tg_payload(),
                         headers=_headers("telegram"))

    assert first.status_code == 200
    assert first.json()["status"] == "accepted"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    cases = _all_cases()
    assert len(cases) == 1
    outcomes = sorted(event.outcome.value for event in _all_events())
    assert outcomes == ["accepted", "duplicate"]


def test_invalid_payload_rejected_and_journaled(client) -> None:
    """Некорректный payload: 422, карточки нет, попытка зафиксирована."""
    broken = json.dumps({"update_id": 1})  # нет обязательного message
    response = client.post(
        "/webhooks/telegram", content=broken,
        headers={**_headers("telegram"), "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["status"] == "invalid"

    assert _all_cases() == []
    outcomes = [event.outcome.value for event in _all_events()]
    assert outcomes == ["invalid"]


def test_broken_json_is_journaled_not_crashed(client) -> None:
    response = client.post(
        "/webhooks/telegram", content="{не json",
        headers={**_headers("telegram"), "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert _all_cases() == []


def test_opt_out_text_blocks_selling_scenario(client) -> None:
    """Обязательный сценарий: opt-out сразу уводит карточку в opt_out."""
    response = client.post(
        "/webhooks/telegram",
        json=_tg_payload(text="Отпишите меня и не звоните больше"),
        headers=_headers("telegram"),
    )
    assert response.status_code == 200
    case = _all_cases()[0]
    assert case.status == CaseStatus.OPT_OUT
    assert case.consent_status == ConsentStatus.OPT_OUT


def test_website_consent_true_creates_new_case(client) -> None:
    payload = {
        "name": "Мария",
        "phone": "+7 921 555-01-02",
        "email": "maria@company.ru",
        "message": "Нужен расчёт уборки офиса",
        "consent": True,
    }
    response = client.post("/webhooks/website", json=payload,
                           headers=_headers("website"))
    assert response.status_code == 200
    case = _all_cases()[0]
    assert case.status == CaseStatus.NEW
    assert case.consent_status == ConsentStatus.OPT_IN


def test_wrong_token_is_rejected_before_any_write(client) -> None:
    response = client.post(
        "/webhooks/telegram", json=_tg_payload(),
        headers={"X-Webhook-Token": "wrong-token"},
    )
    assert response.status_code == 401
    assert _all_cases() == []
    assert _all_events() == []


def test_avito_event_accepted(client) -> None:
    payload = {
        "event_id": "987654",
        "chat_id": "chat-1",
        "listing_id": "svc-cleaning-01",
        "user": {"name": "Ольга", "phone": "+7 911 222-33-44"},
        "message": "Здравствуйте, делаете ли уборку после ремонта?",
    }
    response = client.post("/webhooks/avito", json=payload, headers=_headers("avito"))
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    case = _all_cases()[0]
    assert case.external_event_id == "avito-987654"
