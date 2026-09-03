"""Тесты самой mock-CRM: идемпотентность, авторизация, режимы отказа.

Тесты идут по HTTP к реальному сервису mock-crm из docker compose.
"""

import httpx
import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def clean_crm():
    settings = get_settings()
    httpx.post(
        "/".join((settings.mock_crm_url, "__chaos", "reset")),
        headers={"X-CRM-Token": settings.mock_crm_token},
    )


def _headers() -> dict:
    settings = get_settings()
    return {"X-CRM-Token": settings.mock_crm_token}


def _url(path: str) -> str:
    return "/".join((get_settings().mock_crm_url, path))


def _bundle() -> dict:
    return {
        "contact": {"name": "Мария", "phone": "+7 921 555-01-02"},
        "deal": {"title": "Уборка помещений", "source": "website",
                 "quality_score": 80, "case_id": "c-1"},
        "draft": {"reply_text": "Здравствуйте!", "clarifying_question": "Какая площадь?",
                  "next_action": "Позвонить"},
        "task": {"title": "Связаться", "due_at": "2026-09-03T12:00:00+00:00"},
    }


def test_create_deal_and_idempotent_repeat() -> None:
    first = httpx.post(_url("deals"), json=_bundle(),
                       headers={**_headers(), "Idempotency-Key": "deal-case-1"})
    second = httpx.post(_url("deals"), json=_bundle(),
                        headers={**_headers(), "Idempotency-Key": "deal-case-1"})

    assert first.status_code == 200
    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    # У повтора ID не возвращается: сделка не создавалась.
    assert second.json()["deal_id"] is None

    listing = httpx.get(_url("deals"), headers=_headers())
    assert len(listing.json()["deals"]) == 1


def test_different_keys_create_different_deals() -> None:
    for key in ("deal-case-1", "deal-case-2"):
        httpx.post(_url("deals"), json=_bundle(),
                   headers={**_headers(), "Idempotency-Key": key})
    listing = httpx.get(_url("deals"), headers=_headers())
    assert len(listing.json()["deals"]) == 2


def test_wrong_token_rejected() -> None:
    response = httpx.post(
        _url("deals"), json=_bundle(),
        headers={"X-CRM-Token": "wrong", "Idempotency-Key": "k"},
    )
    assert response.status_code == 401


def test_missing_idempotency_key_rejected() -> None:
    response = httpx.post(_url("deals"), json=_bundle(), headers=_headers())
    assert response.status_code == 400


def test_chaos_429_then_recovery() -> None:
    armed = httpx.post(_url("__chaos/fail_429"), params={"times": 1},
                       headers=_headers())
    assert armed.json()["status"] == "armed"

    blocked = httpx.post(_url("deals"), json=_bundle(),
                         headers={**_headers(), "Idempotency-Key": "deal-chaos"})
    assert blocked.status_code == 429

    ok = httpx.post(_url("deals"), json=_bundle(),
                    headers={**_headers(), "Idempotency-Key": "deal-chaos"})
    assert ok.status_code == 200
    assert ok.json()["deduplicated"] is False
