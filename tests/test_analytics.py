"""Тесты метрик аналитики на контролируемом сценарии."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import LeadCase
from app.worker.pipeline import run_once


def _tg(update_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 1756800000,
            "from": {"id": 1, "first_name": "Тест"},
            "text": text,
        },
    }


def _post(client, payload) -> None:
    response = client.post(
        "/webhooks/telegram",
        json=payload,
        headers={"X-Webhook-Token": get_settings().webhook_token_telegram},
    )
    assert response.status_code == 200


def _analytics(client) -> dict:
    response = client.get("/analytics", headers={"X-Admin-Token": get_settings().admin_token})
    assert response.status_code == 200
    return response.json()


def test_metrics_on_controlled_scenario(client) -> None:
    # Два события + один дубль = 3 события, 2 уникальных лида.
    _post(client, _tg(9101, "Срочно нужна уборка офиса, бюджет 30000 руб"))
    _post(client, _tg(9101, "Срочно нужна уборка офиса, бюджет 30000 руб"))
    _post(client, _tg(9102, "Отпишите меня от рассылки"))

    with SessionLocal() as session:
        run_once(session)

    data = _analytics(client)
    assert data["events_received"] == 3
    assert data["unique_leads"] == 2
    assert data["duplicates"] == 1
    assert data["qualified"] == 1
    assert data["opt_out"] == 1
    assert data["by_source"] == {"telegram": 2}
    assert data["avg_time_to_draft_seconds"] is not None
    assert data["avg_time_to_draft_seconds"] >= 0

    # Follow-up в будущем (высокая срочность => +1 час) — просрочки нет.
    assert data["overdue_follow_ups"] == 0


def test_overdue_follow_ups_counted(client) -> None:
    _post(client, _tg(9103, "Нужна уборка коттеджа на следующей неделе"))
    with SessionLocal() as session:
        run_once(session)
        case = next(iter(session.execute(select(LeadCase)).scalars()))
        case.follow_up_due_at = datetime.now(tz=UTC) - timedelta(hours=1)
        session.commit()

    data = _analytics(client)
    assert data["overdue_follow_ups"] == 1


def test_analytics_requires_admin(client) -> None:
    assert client.get("/analytics").status_code == 401


def test_dashboard_is_served(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "панель менеджера" in response.text
